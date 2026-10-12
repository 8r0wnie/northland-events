"""Agile Ticketing (AudienceView Professional) public show feed.

Venues that sell through Agile publish every performance as a public JSON
feed, no key:

    GET https://prod3.agileticketing.net/websales/feed.ashx?guid=<entry-point guid>&format=json
    -> {"ArrayOfEvent": [{Name, StartDate, EndDate (naive local), DateTBD,
        ShortDescription, Venue{Name, Address1, City, State}, InfoLink, BuyLink,
        EventImage, SalesState}]}

The guid is the `epguid`/`guid` in the venue's "buy tickets" links.
`events_url: agile:<guid>` pins it. `agile:<guid>|venue=<text>` keeps only
performances whose venue name contains <text>. Zeitgeist uses
`|venue=Teatro`, so the owner gets live theater and not Zinema's film
showtimes (owner decision 2026-10-12). Pin-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from models import Event, clean_text, parse_dt
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

FEED = "https://prod3.agileticketing.net/websales/feed.ashx?guid={guid}&format=json"
HORIZON_DAYS = 200


def parse_spec(events_url: str) -> tuple[str, str]:
    """'agile:<guid>|venue=Teatro' -> (guid, 'teatro')."""
    spec = (events_url or "").split(":", 1)[1] if (events_url or "").lower().startswith("agile:") else ""
    guid, _, rest = spec.partition("|")
    venue = rest.split("=", 1)[1].strip().lower() if rest.lower().startswith("venue=") else ""
    return guid.strip(), venue


def events_from_feed(data: dict, venue_filter: str = "", now: datetime | None = None) -> list[Event]:
    now = now or datetime.now()
    out = []
    for row in data.get("ArrayOfEvent") or []:
        v = row.get("Venue") or {}
        vname = clean_text(v.get("Name") or "")
        if venue_filter and venue_filter not in vname.lower():
            continue
        if row.get("DateTBD"):
            continue
        start = parse_dt(row.get("StartDate"))
        if not start or not (now - timedelta(days=1) <= start <= now + timedelta(days=HORIZON_DAYS)):
            continue
        end = parse_dt(row.get("EndDate"))
        addr = ", ".join(x for x in [clean_text(v.get("Address1") or ""), clean_text(v.get("City") or ""),
                                     clean_text(v.get("State") or "")] if x)
        out.append(Event(
            source_id="", title=clean_text(row.get("Name") or ""), start=start,
            end=end if end and end > start else None,
            venue=vname, address=addr, city=clean_text(v.get("City") or ""),
            url=row.get("InfoLink") or row.get("BuyLink") or "",
            description=row.get("ShortDescription") or "",
            image=row.get("EventImage") or "", category="arts",
        ))
    return out


class AgileAdapter:
    name = "agile"

    def scrape(self, source: Source) -> AdapterResult:
        guid, venue = parse_spec(source.events_url)
        if not guid:
            return AdapterResult(self.name, ok=False, detail="events_url must be agile:<guid>")
        r = fetch.get(FEED.format(guid=guid))
        if r is None:
            return AdapterResult(self.name, ok=False, detail="feed unavailable")
        try:
            data = r.json()
        except ValueError:
            return AdapterResult(self.name, ok=False, detail="feed was not JSON")
        events = tag_from_source(events_from_feed(data, venue), source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} performances"
                                    + (f" at venues matching '{venue}'" if venue else ""))

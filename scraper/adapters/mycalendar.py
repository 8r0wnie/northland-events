"""'My Calendar' WordPress plugin adapter.

My Calendar (by Joe Dolan) exposes a public REST route that returns
recurrence-expanded occurrences:

    GET /wp-json/my-calendar/v1/events?from=YYYY-MM-DD&to=YYYY-MM-DD

Response is a JSON object keyed by date; each entry has occur_begin/occur_end
plus event_title / event_desc / event_location / event_street / event_city / ...
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

MONTHS_AHEAD = 6


def _api(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/wp-json/my-calendar/v1/events"


def _strip(s: str) -> str:
    s = s or ""
    return HTMLParser(s).text() if "<" in s else s


def _to_event(node: dict) -> Event | None:
    start = parse_dt(node.get("occur_begin") or node.get("event_begin"))
    if not start:
        return None
    end = parse_dt(node.get("occur_end"))
    addr = ", ".join(p for p in [
        clean_text(node.get("event_street", "")), clean_text(node.get("event_street2", "")),
        clean_text(node.get("event_city", "")), clean_text(node.get("event_state", "")),
    ] if p)
    return Event(
        source_id="",
        title=clean_text(node.get("event_title") or ""),
        start=start,
        end=end,
        all_day=str(node.get("event_time", "")).startswith("00:00") and not node.get("occur_end"),
        venue=clean_text(node.get("event_label") or node.get("event_location_name") or ""),
        address=addr,
        city=clean_text(node.get("event_city") or ""),
        state=clean_text(node.get("event_state") or ""),
        url=clean_text(node.get("event_link") or node.get("event_url") or ""),
        description=clean_text(_strip(node.get("event_desc") or node.get("event_short") or "")),
        image=clean_text(node.get("event_image") or ""),
        category="community",
    )


class MyCalendarAdapter:
    name = "mycalendar"

    def scrape(self, source: Source) -> AdapterResult:
        api = _api(source.url)
        start = date.today()
        end = start + timedelta(days=31 * MONTHS_AHEAD)
        r = fetch.get(api, params={"from": start.isoformat(), "to": end.isoformat()})
        if r is None:
            return AdapterResult(self.name, ok=False, detail="no My Calendar API")
        try:
            payload = r.json()
        except ValueError:
            return AdapterResult(self.name, ok=False, detail="non-JSON response")
        if not isinstance(payload, dict):
            return AdapterResult(self.name, ok=False, detail="unexpected payload")

        events: list[Event] = []
        for day_list in payload.values():
            for node in (day_list or []):
                ev = _to_event(node)
                if ev:
                    events.append(ev)

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events")

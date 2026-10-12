"""Eventbrite organizer adapter: every upcoming event one organizer publishes.

An organizer page (eventbrite.com/o/<slug>-<id>) loads its list from a public
JSON endpoint the page calls for itself, no key or login:

    GET https://www.eventbrite.com/organizer-profile/api/organizers/<id>/events-from-date/
        ?from_date=YYYY-MM-DD&page=N&page_size=50&order_by=start_asc&include_started=true
    -> {"events": [{name, url, start_date, start_time, end_date, end_time,
                    is_cancelled, summary, image{url}, primary_venue{name, address{...}}}],
        "has_more": bool}

The organizer id comes from `events_url: eventbrite:<id>`, from an
eventbrite.com/o/... URL in `events_url`, or from the first eventbrite.com/o/
link on the venue's own page. `start_date`/`start_time` are local wall time.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from models import Event, clean_text, parse_dt
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

API = ("https://www.eventbrite.com/organizer-profile/api/organizers/{oid}/events-from-date/"
       "?from_date={d}&page={p}&page_size=50&order_by=start_asc&include_started=true")
_ORG = re.compile(r"eventbrite\.[a-z.]+/o/(?:[a-z0-9-]*-)?(\d{6,})", re.I)
MAX_PAGES = 6


def _organizer_id(source: Source) -> str | None:
    ev = source.events_url or ""
    if ev.lower().startswith("eventbrite:"):
        return ev.split(":", 1)[1].strip() or None
    m = _ORG.search(ev)
    if m:
        return m.group(1)
    html = fetch.get_text(source.target)
    m = _ORG.search(html or "")
    return m.group(1) if m else None


class EventbriteAdapter:
    name = "eventbrite"

    def scrape(self, source: Source) -> AdapterResult:
        oid = _organizer_id(source)
        if not oid:
            return AdapterResult(self.name, detail="no Eventbrite organizer found")
        events: list[Event] = []
        today = date.today().isoformat()
        for page in range(1, MAX_PAGES + 1):
            r = fetch.get(API.format(oid=oid, d=today, p=page), headers={"Accept": "application/json"})
            if r is None:
                break
            try:
                data = r.json()
            except ValueError:
                break
            for e in data.get("events") or []:
                if e.get("is_cancelled") or e.get("is_online_event"):
                    continue
                start = parse_dt(f"{e.get('start_date', '')} {e.get('start_time', '')}".strip())
                if not start:
                    continue
                end = parse_dt(f"{e.get('end_date', '')} {e.get('end_time', '')}".strip())
                v = e.get("primary_venue") or {}
                addr = v.get("address") or {}
                events.append(Event(
                    source_id="",
                    title=clean_text(e.get("name") or ""),
                    start=start,
                    end=end if end and end > start else None,
                    venue=clean_text(v.get("name") or ""),
                    city=clean_text(addr.get("city") or ""),
                    state=clean_text(addr.get("region") or ""),
                    address=clean_text(addr.get("localized_address_display") or ""),
                    url=e.get("url") or "",
                    description=clean_text(e.get("summary") or ""),
                    image=((e.get("image") or {}).get("url") or ""),
                    category="other",
                ))
            if not data.get("has_more"):
                break
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events (organizer {oid})")

"""ChamberOrganizer / "chamberwidgets" adapter.

The calendar widget on these chamber sites posts month-by-month to a public API:

    POST https://auth.chamberwidgets.com/cn-api/org/calendar/events
    form  searchValues=&org_id=<CODE>&year=<YYYY>&month=<MM>&day=

and gets back a JSON array of fully-populated event objects (name, description,
start/end date + time, location_name, address, city, state, cost, image).

The org code (e.g. HIBB) is discovered from `org_id=<CODE>` in the source's
calendar page HTML.
"""
from __future__ import annotations

import re
from datetime import date

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

API = "https://auth.chamberwidgets.com/cn-api/org/calendar/events"
MONTHS_AHEAD = 6
_ORG = re.compile(r"org_id=([A-Za-z0-9_-]+)")


def _org_code(source: Source) -> str | None:
    for page in (source.target, source.url):
        html = fetch.get_text(page)
        if html:
            m = _ORG.search(html)
            if m:
                return m.group(1)
    return None


def _months(n: int):
    y, m = date.today().year, date.today().month
    for _ in range(n):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _clean_date(s: str) -> str:
    return "" if not s or s.startswith("0000") else s


def _to_event(node: dict) -> Event | None:
    start = parse_dt(f"{_clean_date(node.get('startdate',''))} {node.get('start_time') or node.get('cd_start_time') or ''}".strip())
    if not start:
        return None
    end_date = _clean_date(node.get("enddate", "")) or _clean_date(node.get("startdate", ""))
    end = parse_dt(f"{end_date} {node.get('end_time') or node.get('cd_end_time') or ''}".strip()) if end_date else None
    from selectolax.parser import HTMLParser
    desc = node.get("description") or node.get("details") or ""
    desc = HTMLParser(desc).text() if "<" in desc else desc
    addr = ", ".join(p for p in [
        clean_text(node.get("address", "")), clean_text(node.get("address2", "")),
        clean_text(node.get("city", "")), clean_text(node.get("state", "")),
    ] if p)
    return Event(
        source_id="",
        title=clean_text(node.get("name") or ""),
        start=start,
        end=end,
        venue=clean_text(node.get("location_name") or ""),
        address=addr,
        city=clean_text(node.get("city") or ""),
        state=clean_text(node.get("state") or ""),
        url=clean_text(node.get("more_info_link") or node.get("event_reg_link") or ""),
        description=clean_text(desc),
        price=clean_text(node.get("event_cost") or ""),
        image=clean_text(node.get("cal_image_full") or node.get("cal_image_url") or ""),
    )


class ChamberOrganizerAdapter:
    name = "chamberorganizer"

    def scrape(self, source: Source) -> AdapterResult:
        code = _org_code(source)
        if not code:
            return AdapterResult(self.name, ok=False, detail="no org_id found")

        seen: set[int] = set()
        events: list[Event] = []
        for year, month in _months(MONTHS_AHEAD):
            r = fetch.post_text(API, data={"searchValues": f"&org_id={code}&year={year}&month={month:02d}&day="},
                                headers={"Origin": f"https://{fetch._host(source.url)}"})
            if not r:
                continue
            try:
                import json
                rows = json.loads(r)
            except ValueError:
                continue
            for node in rows:
                eid = node.get("eventid")
                if eid in seen:
                    continue
                seen.add(eid)
                ev = _to_event(node)
                if ev:
                    events.append(ev)

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events (org {code})")

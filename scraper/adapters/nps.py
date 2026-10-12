"""National Park Service unit calendars (Grand Portage, Apostle Islands, Voyageurs).

Every nps.gov park calendar page loads its events from a public service, no
key (the api.nps.gov developer API needs one, so we don't use it):

    GET https://home.nps.gov/common/components/nps/eventcalendar/EventCalendarService.cfc
        ?method=getEvents&parkCode=<code>&dateStart=YYYY-MM-DD&dateEnd=YYYY-MM-DD
        &expandRecurring=true&pageSize=50&pageNumber=N
    -> {"total": n, "data": [{title, date, times[{timeStart, timeEnd}], isAllDay,
        location, description (HTML), infoURL, id}]}

With expandRecurring each occurrence is its own record (`date`). Park code
from `events_url: nps:<code>` or the first path segment of an nps.gov URL
(nps.gov/grpo/... -> grpo). Northern parks post mostly in summer, so an
empty result in winter is normal.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlparse

from models import Event, clean_text, parse_dt
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

API = ("https://home.nps.gov/common/components/nps/eventcalendar/EventCalendarService.cfc"
       "?method=getEvents&parkCode={code}&dateStart={a}&dateEnd={b}"
       "&expandRecurring=true&pageSize=50&pageNumber={n}")
HORIZON_DAYS = 200
MAX_PAGES = 10
_TAGS = re.compile(r"<[^>]+>")


def _park_code(source: Source) -> str | None:
    ev = source.events_url or source.url
    if ev.lower().startswith("nps:"):
        return ev.split(":", 1)[1].strip().lower() or None
    p = urlparse(ev)
    if p.netloc.endswith("nps.gov"):
        seg = [s for s in p.path.split("/") if s]
        if seg and re.fullmatch(r"[a-z]{4}", seg[0]):
            return seg[0]
    return None


class NpsAdapter:
    name = "nps"

    def scrape(self, source: Source) -> AdapterResult:
        code = _park_code(source)
        if not code:
            return AdapterResult(self.name, detail="no NPS park code")
        a = date.today()
        b = a + timedelta(days=HORIZON_DAYS)
        rows: list[dict] = []
        for n in range(1, MAX_PAGES + 1):
            r = fetch.get(API.format(code=code, a=a, b=b, n=n))
            try:
                data = r.json() if r is not None else {}
            except ValueError:
                break
            batch = data.get("data") or []
            rows += batch
            if len(rows) >= int(data.get("total") or 0) or not batch:
                break
        events: list[Event] = []
        for e in rows:
            day = e.get("date")
            times = e.get("times") or [{}]
            t0 = times[0] if times else {}
            all_day = bool(e.get("isAllDay")) or not t0.get("timeStart")
            start = parse_dt(day if all_day else f"{day} {t0.get('timeStart')}")
            if not start:
                continue
            end = None if all_day or not t0.get("timeEnd") else parse_dt(f"{day} {t0['timeEnd']}")
            events.append(Event(
                source_id="", title=clean_text(e.get("title") or ""), start=start,
                end=end, all_day=all_day, venue=clean_text(e.get("location") or ""),
                url=e.get("infoURL") or source.url,
                description=clean_text(_TAGS.sub(" ", e.get("description") or "")),
                price="Free" if e.get("isFree") else "", category="other",
            ))
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events (park {code}, {len(rows)} records)")

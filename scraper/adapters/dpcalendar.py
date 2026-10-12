"""DPCalendar (Joomla, Digital Peak) adapter, e.g. City of Babbitt.

The calendar page draws a FullCalendar grid from a public JSON endpoint:

    GET <root>/index.php?option=com_dpcalendar&view=events&limit=0&format=raw
        &module_id=<m>&Itemid=<i>&filter[calendars][]=<cal id> (repeated)
        &list[start-date]=YYYY-MM-DDT00:00:00&list[end-date]=...
    -> {"success": true, "data": {"events": [{id, title, start, end, allDay,
        url, location[], description (tooltip HTML)}]}}

The endpoint returns nothing unless the calendar ids are passed, so they are
read from the page itself (`g-13` = a Google calendar wired into DPCalendar,
plain digits = native DPCalendar calendars), along with Itemid/module_id.
Recurring series arrive already expanded. main.trim_frequent applies the
project's frequent-series rule (weekly-or-more programs show 60 days ahead);
MAX_PER_TITLE is only a hard backstop.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse

from models import Event, clean_text, parse_dt
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

HORIZON_DAYS = 200
WINDOW_DAYS = 60
MAX_PER_TITLE = 60
_CAL_IDS = re.compile(r"(?<![\w-])(g-\d+)(?![\w-])")
_NATIVE_IDS = re.compile(r"calendars?\]?\[?\]?[=\":\[\s]+\"?(\d+)")
_ITEMID = re.compile(r"Itemid[=\":\s]+(\d+)")
_MODULE = re.compile(r"module[_-]id[=\":\s]+(\d+)")
_TAGS = re.compile(r"<[^>]+>")


class DPCalendarAdapter:
    name = "dpcalendar"

    def scrape(self, source: Source) -> AdapterResult:
        html = fetch.get_text(source.target)
        if not html or "dpcalendar" not in html.lower():
            return AdapterResult(self.name, detail="no DPCalendar on page")
        p = urlparse(source.target)
        root = f"{p.scheme}://{p.netloc}"
        cals = list(dict.fromkeys(_CAL_IDS.findall(html) or _NATIVE_IDS.findall(html)))
        if not cals:
            return AdapterResult(self.name, detail="no calendar ids on page")
        item = _ITEMID.search(html)
        mod = _MODULE.search(html)
        q = "index.php?option=com_dpcalendar&view=events&limit=0&format=raw"
        if mod:
            q += f"&module_id={mod.group(1)}"
        if item:
            q += f"&Itemid={item.group(1)}"
        q += "".join(f"&filter%5Bcalendars%5D%5B%5D={c}" for c in cals)

        raw, seen = [], set()
        start = date.today()
        end = start + timedelta(days=HORIZON_DAYS)
        while start < end:
            stop = min(end, start + timedelta(days=WINDOW_DAYS))
            r = fetch.get(f"{root}/{q}&list%5Bstart-date%5D={start}T00:00:00"
                          f"&list%5Bend-date%5D={stop}T00:00:00",
                          headers={"X-Requested-With": "XMLHttpRequest"})
            try:
                rows = ((r.json() if r is not None else {}).get("data") or {}).get("events") or []
            except ValueError:
                rows = []
            for e in rows:
                if e.get("id") not in seen:
                    seen.add(e.get("id"))
                    raw.append(e)
            start = stop

        per_title: Counter = Counter()
        events: list[Event] = []
        for e in sorted(raw, key=lambda e: e.get("start") or ""):
            title = clean_text(e.get("title") or "")
            st = parse_dt(e.get("start"))
            if not title or not st:
                continue
            per_title[title.lower()] += 1
            if per_title[title.lower()] > MAX_PER_TITLE:
                continue
            loc = e.get("location") or []
            venue = clean_text(loc[0].get("title", "")) if loc and isinstance(loc[0], dict) else ""
            desc = clean_text(_TAGS.sub(" ", e.get("description") or ""))
            events.append(Event(
                source_id="", title=title, start=st, end=parse_dt(e.get("end")),
                all_day=bool(e.get("allDay")), venue=venue,
                url=urljoin(root, e.get("url") or "") or source.target,
                description=desc, category="community",
            ))
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events from {len(cals)} calendar(s)")

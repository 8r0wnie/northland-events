"""DocAccess "WebCalendar" adapter (City of Duluth and similar .NET municipal sites).

The events page posts one request per month:

    POST <root>/WebCalendar/CreateCalendarAndEvents
    form  chosenCategories=1&chosenCategories=2&...&selectedMonth=<M>&selectedYear=<Y>&view=calendar

and returns an HTML month grid of `.day` cells, each containing
`<button class="calendaritem" onclick="OpenEventDetails(<id>)">
   <strong>Title</strong><br>3:00PM - 5:00PM</button>`.
The day number comes from the cell's `.daynumber`.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

MONTHS_AHEAD = 6
CATEGORIES = "&".join(f"chosenCategories={i}" for i in range(1, 20))
_TIME = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)", re.I)
_EID = re.compile(r"OpenEventDetails\((\d+)\)")


def _months(n: int):
    y, m = date.today().year, date.today().month
    for _ in range(n):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


class WebCalendarAdapter:
    name = "webcalendar"

    def _endpoint(self, source: Source) -> str:
        from urllib.parse import urlparse
        p = urlparse(source.url)
        return f"{p.scheme}://{p.netloc}/WebCalendar/CreateCalendarAndEvents"

    def scrape(self, source: Source) -> AdapterResult:
        endpoint = self._endpoint(source)
        root = endpoint.split("/WebCalendar/")[0]
        events: list[Event] = []
        ok_any = False

        for i, (year, month) in enumerate(_months(MONTHS_AHEAD)):
            body = f"{CATEGORIES}&selectedMonth={month}&selectedYear={year}&view=calendar"
            html = fetch.post_text(endpoint, data=body,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
            if not html:
                if i == 0:
                    return AdapterResult(self.name, ok=False, detail="no WebCalendar endpoint")
                continue
            if "calendaritem" not in html:
                continue
            ok_any = True
            tree = HTMLParser(html)
            for cell in tree.css(".day"):
                dn = cell.css_first(".daynumber")
                if not dn or not dn.text(strip=True).isdigit():
                    continue
                day = int(dn.text(strip=True))
                is_other = "otherday" in (cell.attributes.get("class") or "")
                for btn in cell.css(".calendaritem"):
                    title_el = btn.css_first("strong")
                    title = clean_text(title_el.text()) if title_el else clean_text(btn.text())
                    if not title:
                        continue
                    if is_other:
                        continue  # spillover day from an adjacent month — caught in its own pass
                    times = _TIME.findall(btn.text())
                    start = parse_dt(f"{year}-{month:02d}-{day:02d} {times[0] if times else ''}".strip())
                    if not start:
                        continue
                    end = parse_dt(f"{year}-{month:02d}-{day:02d} {times[1]}") if len(times) > 1 else None
                    eid = _EID.search(btn.attributes.get("onclick") or "")
                    events.append(Event(
                        source_id="",
                        title=title,
                        start=start,
                        end=end,
                        all_day=not times,
                        url=f"{root}/event-calendar/" + (f"?eventId={eid.group(1)}" if eid else ""),
                        category="community",
                    ))

        if not ok_any:
            return AdapterResult(self.name, ok=False, detail="no WebCalendar endpoint")
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events), detail=f"{len(events)} events")

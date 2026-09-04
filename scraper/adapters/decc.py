"""The DECC — WordPress "swim-events-calendar" plugin.

The events page renders `<section class="event-list-item">` blocks with
`span.event-date` ("Sep04| 8:00 pm"), `span.slide-venue` (the room), and
`h2.entry-title`. No year in the date string — the list is chronological, so we
roll the year forward when the month goes backwards.
"""
from __future__ import annotations

import re
from datetime import datetime

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text, infer_category
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

_DATE = re.compile(r"([A-Za-z]{3,9})\s*(\d{1,2})\s*\|\s*(.+)")
_CAT = {
    "concert": "music", "music": "music", "entertainment": "arts",
    "theatre": "arts", "comedy": "arts", "family": "family",
    "sport": "sports-rec", "hockey": "sports-rec", "expo": "community",
    "convention": "community", "holiday": "holiday",
}


def _category_from_classes(section) -> str:
    classes = section.attributes.get("class", "")
    for token in re.findall(r"category-([a-z-]+)", classes):
        for needle, cat in _CAT.items():
            if needle in token:
                return cat
    return ""


class DeccAdapter:
    name = "decc"

    def scrape(self, source: Source) -> AdapterResult:
        html = fetch.get_text(source.target)
        if not html or "event-list-item" not in html:
            html = fetch.render(source.target, wait_selector=".event-list-item") or html
        if not html:
            return AdapterResult(self.name, ok=False, detail="fetch failed")
        tree = HTMLParser(html)

        now = datetime.now()
        events: list[Event] = []
        prev_month = now.month
        year = now.year

        for section in tree.css(".event-list-item"):
            de = section.css_first("span.event-date")
            te = section.css_first("h2.entry-title")
            if not de or not te:
                continue
            m = _DATE.match(clean_text(de.text()))
            if not m:
                continue
            month_txt, day, time_txt = m.groups()
            probe = parse_dt(f"{month_txt} {day} 2000")
            if not probe:
                continue
            if probe.month < prev_month - 1:      # wrapped past December
                year += 1
            prev_month = probe.month
            start = parse_dt(f"{month_txt} {day} {year} {clean_text(time_txt)}")
            if not start:
                continue

            venue_el = section.css_first("span.slide-venue")
            link = section.css_first("a.post-thumbnail, a.read-more, h2 a")
            desc_el = section.css_first("div.entry-content")
            title = clean_text(te.text())
            events.append(Event(
                source_id="",
                title=title,
                start=start,
                venue=clean_text(venue_el.text()) if venue_el else "",
                url=(link.attributes.get("href") if link else "") or source.target,
                description=clean_text(desc_el.text())[:600] if desc_el else "",
                category=infer_category(title, fallback=_category_from_classes(section) or "other"),
            ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events")

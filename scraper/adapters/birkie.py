"""American Birkebeiner Ski Foundation — birkie.com/calendar/.

Static WordPress page, no events plugin and no JSON-LD (that's why every
generic adapter came back empty during triage) — it's hand-built wp-block
markup: one `<h2 class="calendar-list__month-separator">` per month, followed
by one `<div class="wp-block-columns">` per event, each holding an
`<h3><a>title</a></h3>`, a date wrapped in `<em>` inside the first paragraph,
and a longer description in the paragraph after it. Fully server-rendered —
no headless render needed.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text, infer_category
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

# "November 26-28, 2026" (range) vs "Saturday, September 26, 2026" (single day).
# Range must be tried first — a single-day match would otherwise grab just the
# first day of a range and silently drop the end date.
_RANGE = re.compile(r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*[-–]\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})")
_SINGLE = re.compile(r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})")


def _parse_date_text(text: str):
    m = _RANGE.search(text)
    if m:
        month, d1, d2, year = m.groups()
        return parse_dt(f"{month} {d1} {year}"), parse_dt(f"{month} {d2} {year}")
    m = _SINGLE.search(text)
    if m:
        month, day, year = m.groups()
        return parse_dt(f"{month} {day} {year}"), None
    return None, None


class BirkieAdapter:
    name = "birkie"

    def scrape(self, source: Source) -> AdapterResult:
        html = fetch.get_text(source.target)
        if not html or "event-list" not in html:
            return AdapterResult(self.name, ok=False, detail="fetch failed or markup changed")
        tree = HTMLParser(html)

        events: list[Event] = []
        for block in tree.css(".event-list .wp-block-columns"):
            title_el = block.css_first("h3.wp-block-heading a")
            if not title_el:
                continue
            title = clean_text(title_el.text())
            if not title:
                continue

            paras = block.css("p.wp-block-paragraph")
            if not paras:
                continue
            date_el = paras[0].css_first("em") or paras[0]
            start, end = _parse_date_text(clean_text(date_el.text()))
            if not start:
                continue

            desc = ""
            for p in paras[1:]:
                t = clean_text(p.text())
                if t:
                    desc = t[:600]
                    break

            events.append(Event(
                source_id="",
                title=title,
                start=start,
                end=end,
                all_day=True,
                url=title_el.attributes.get("href") or source.url,
                description=desc,
                category=infer_category(title, desc, fallback="sports-rec"),
            ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events")

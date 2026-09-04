"""North House Folk School — northhouse.org/events.

Real schema.org structured data, but old-style Microdata
(`itemscope itemtype="http://schema.org/EducationEvent"`) rather than the
`<script type="application/ld+json">` blocks jsonld.py looks for — that's why
the generic adapter came back empty during triage. Fully server-rendered
static HTML, no headless render needed.
"""
from __future__ import annotations

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text, infer_category
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

BASE = "https://northhouse.org"


def _parse_date_range(text: str):
    text = clean_text(text)
    for sep in (" - ", " – "):
        if sep in text:
            left, right = text.split(sep, 1)
            return parse_dt(left), parse_dt(right)
    return parse_dt(text), None


class NorthHouseAdapter:
    name = "northhouse"

    def scrape(self, source: Source) -> AdapterResult:
        html = fetch.get_text(source.target)
        if not html or "EducationEvent" not in html:
            return AdapterResult(self.name, ok=False, detail="fetch failed or markup changed")
        tree = HTMLParser(html)

        events: list[Event] = []
        for card in tree.css('div[itemtype="http://schema.org/EducationEvent"]'):
            title_el = card.css_first('h3[itemprop="name"] a, [itemprop="name"] a')
            if not title_el:
                continue
            title = clean_text(title_el.text())
            if not title:
                continue

            date_el = card.css_first(".titleSupportingDate")
            if not date_el:
                continue
            start, end = _parse_date_range(date_el.text())
            if not start:
                continue

            href = title_el.attributes.get("href") or ""
            url = href if href.startswith("http") else BASE + href
            desc_el = card.css_first('[itemprop="description"]')
            desc = clean_text(desc_el.text())[:600] if desc_el else ""

            events.append(Event(
                source_id="",
                title=title,
                start=start,
                end=end,
                all_day=True,
                venue="North House Folk School",
                city="Grand Marais",
                url=url,
                description=desc,
                category=infer_category(title, desc, fallback="education"),
            ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events")

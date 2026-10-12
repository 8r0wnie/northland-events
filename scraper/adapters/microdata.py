"""schema.org Event *microdata* (itemscope/itemprop attributes) on a listing page.

`jsonld` only reads <script type="application/ld+json">. Plenty of platforms
mark events up with microdata instead, e.g. GrowthZone "Hub" chamber
calendars (business.<chamber>.com/calendar: cards with
itemtype=schema.org/Event, startDate "10/4/2026 10:00:00 AM") and North
House Folk School (EducationEvent; it has its own adapter). This reads every
element whose itemtype ends in "Event" and its nested itemprops:
name, startDate, endDate, url, location (name/address), description/about.

Cheap (one fetch, no browser), so it sits in the generic chain after jsonld.
GrowthZone Hub sites usually have two lists, /calendar (chamber) and
/communitycalendar; pin `events_url` to the community one when both exist,
or add a second source.
"""
from __future__ import annotations

from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from models import Event, clean_text, parse_dt
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch


def _prop(node, name: str):
    """First descendant itemprop=name that belongs to this item (not a nested one)."""
    # selectolax hands out a fresh Node object per access, so compare mem_id
    # (identity of the underlying element), never `is`
    target = node.mem_id
    for n in node.css(f'[itemprop="{name}"]'):
        owner = n.parent
        while owner is not None and owner.mem_id != target and "itemscope" not in owner.attributes:
            owner = owner.parent
        if owner is None or owner.mem_id == target:
            return n
    return None


def _value(n) -> str:
    if n is None:
        return ""
    a = n.attributes
    for key in ("content", "datetime", "href", "src"):
        if a.get(key):
            return a[key]
    return clean_text(n.text(separator=" "))


class MicrodataAdapter:
    name = "microdata"

    def scrape(self, source: Source) -> AdapterResult:
        url = source.target
        html = fetch.get_text(url)
        if not html or "itemscope" not in html:
            return AdapterResult(self.name, detail="no microdata")
        tree = HTMLParser(html)
        events: list[Event] = []
        seen = set()
        for item in tree.css("[itemscope][itemtype]"):
            if not item.attributes.get("itemtype", "").rstrip("/").endswith("Event"):
                continue
            title = clean_text(_value(_prop(item, "name")))
            start = parse_dt(_value(_prop(item, "startDate")))
            if not title or not start or (title.lower(), start) in seen:
                continue
            seen.add((title.lower(), start))
            link = _prop(item, "url")
            href = (link.attributes.get("href") if link is not None else "") or ""
            if not href:
                a = item.css_first("a[href]")
                href = a.attributes.get("href", "") if a else ""
            loc = _prop(item, "location")
            venue = address = ""
            if loc is not None:
                venue = clean_text(_value(_prop(loc, "name"))) or clean_text(loc.text(separator=" "))[:120]
                address = clean_text(_value(_prop(loc, "address")))
            desc = _value(_prop(item, "description")) or _value(_prop(item, "about"))
            events.append(Event(
                source_id="", title=title, start=start,
                end=parse_dt(_value(_prop(item, "endDate"))),
                venue=venue[:120], address=address, url=urljoin(url, href) if href else url,
                description=clean_text(desc), category="other",
            ))
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events")

"""Generic schema.org Event extractor.

Many modern event pages embed <script type="application/ld+json"> with one or
more Event objects (directly, in an array, or under @graph). This adapter pulls
those with no per-site knowledge. It fetches the source's events page, and if
that page links to detail pages it will also sample a few of those.
"""
from __future__ import annotations

import json
from typing import Iterator
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

EVENT_TYPES = {"event", "musicevent", "theaterevent", "festival", "socialevent",
               "sportsevent", "childrensevent", "comedyevent", "danceevent",
               "educationevent", "exhibitionevent", "foodevent", "literaryevent",
               "screeningevent", "visualartsevent", "businessevent"}


def _walk(node) -> Iterator[dict]:
    if isinstance(node, dict):
        if "@graph" in node:
            yield from _walk(node["@graph"])
        t = node.get("@type", "")
        types = [t] if isinstance(t, str) else (t or [])
        if any(str(x).lower() in EVENT_TYPES for x in types):
            yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _text(v) -> str:
    if isinstance(v, dict):
        return clean_text(v.get("name") or v.get("@value") or "")
    if isinstance(v, list):
        return clean_text(", ".join(_text(x) for x in v if _text(x)))
    return clean_text(str(v)) if v is not None else ""


def _location(node) -> tuple[str, str, str]:
    loc = node.get("location")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return _text(loc), "", ""
    venue = _text(loc.get("name"))
    addr = loc.get("address")
    if isinstance(addr, str):
        return venue, addr, ""
    if isinstance(addr, dict):
        street = _text(addr.get("streetAddress"))
        city = _text(addr.get("addressLocality"))
        region = _text(addr.get("addressRegion"))
        full = ", ".join(p for p in [street, city, region] if p)
        return venue, full, city
    return venue, "", ""


def _price(node) -> str:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return ""
    price = offers.get("price")
    if price in (0, "0", "0.00"):
        return "Free"
    if price:
        cur = offers.get("priceCurrency", "USD")
        sym = "$" if cur == "USD" else f"{cur} "
        return f"{sym}{price}"
    return ""


def _to_event(node: dict, page_url: str) -> Event | None:
    start = parse_dt(node.get("startDate"))
    if not start:
        return None
    venue, address, city = _location(node)
    url = _text(node.get("url")) or page_url
    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url", "")
    return Event(
        source_id="",
        title=_text(node.get("name")),
        start=start,
        end=parse_dt(node.get("endDate")),
        venue=venue,
        address=address,
        city=city,
        url=url,
        description=_text(node.get("description")),
        price=_price(node),
        image=clean_text(str(image or "")),
    )


class JsonLdAdapter:
    name = "jsonld"
    MAX_DETAIL_PAGES = 25

    def _from_html(self, html: str, page_url: str) -> list[Event]:
        tree = HTMLParser(html)
        events: list[Event] = []
        for tag in tree.css('script[type="application/ld+json"]'):
            raw = tag.text(strip=False) or ""
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            for node in _walk(data):
                ev = _to_event(node, page_url)
                if ev:
                    events.append(ev)
        return events

    def _candidate_detail_links(self, html: str, base: str) -> list[str]:
        tree = HTMLParser(html)
        host = urlparse(base).netloc
        links: list[str] = []
        seen = set()
        for a in tree.css("a[href]"):
            href = (a.attributes.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(base, href)
            parts = urlparse(full)
            if parts.scheme not in ("http", "https") or parts.netloc != host:
                continue
            path = parts.path.lower()
            if any(k in path for k in ("/event/", "/events/", "/e/", "event-details", "/calendar/event")):
                key = full.split("#")[0].rstrip("/")
                if key not in seen and key != base.split("#")[0].rstrip("/"):
                    seen.add(key)
                    links.append(full)
        return links[: self.MAX_DETAIL_PAGES]

    def scrape(self, source: Source) -> AdapterResult:
        target = source.target
        html = fetch.get_text(target)
        used_render = False
        if not html:
            html = fetch.render(target)
            used_render = True
        if not html:
            return AdapterResult(self.name, ok=False, detail="fetch failed")

        events = self._from_html(html, target)
        if not events and not used_render:
            rendered = fetch.render(target)
            if rendered:
                used_render = True
                events = self._from_html(rendered, target)
                html = rendered

        if not events:
            # Listing page had no inline events — sample detail pages.
            for link in self._candidate_detail_links(html, target):
                sub = fetch.get_text(link)
                if sub:
                    events.extend(self._from_html(sub, link))

        events = tag_from_source(events, source)
        detail = f"{len(events)} events" + (" (rendered)" if used_render else "")
        return AdapterResult(self.name, events=events, ok=bool(events), detail=detail)

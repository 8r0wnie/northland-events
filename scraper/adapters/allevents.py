"""allevents.in adapter — a public event aggregator that ingests Facebook events.

Direct Facebook/Instagram scraping isn't viable (no events API since 2018,
login-walled event tabs, datacenter-IP blocks, ToS). allevents.in aggregates
public Facebook events (plus Eventbrite etc.) per city and exposes them as
schema.org JSON-LD on `allevents.in/<city>/all?page=N`.

Limitation: the listing JSON-LD carries a date but no time of day, so events
come through as all-day.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text, infer_category
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

MAX_PAGES = 6
HORIZON_DAYS = 150


def _ld_events(html: str) -> list[dict]:
    out: list[dict] = []
    for tag in HTMLParser(html).css('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.text() or "null")
        except (ValueError, TypeError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if isinstance(node, dict) and "Event" in str(node.get("@type", "")):
                out.append(node)
    return out


def _place(node: dict) -> tuple[str, str, str]:
    loc = node.get("location")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return clean_text(str(loc or "")), "", ""
    venue = clean_text(loc.get("name", ""))
    addr = loc.get("address")
    if isinstance(addr, dict):
        street = clean_text(addr.get("streetAddress", ""))
        city = clean_text(addr.get("addressLocality", ""))
        return venue, street, city
    return venue, clean_text(str(addr or "")), ""


class AllEventsAdapter:
    name = "allevents"

    def scrape(self, source: Source) -> AdapterResult:
        base = source.url.rstrip("/")
        if not base.endswith("/all"):
            base = base + "/all"

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        horizon = today + timedelta(days=HORIZON_DAYS)
        seen: set[str] = set()
        events: list[Event] = []

        for page in range(1, MAX_PAGES + 1):
            html = fetch.get_text(base, params={"page": page} if page > 1 else None)
            if not html:
                break
            nodes = _ld_events(html)
            if not nodes:
                break
            fresh = 0
            for node in nodes:
                url = clean_text(node.get("url") or "")
                if url in seen:
                    continue
                seen.add(url)
                fresh += 1
                start = parse_dt(node.get("startDate"))
                if not start:
                    continue
                start = start.replace(tzinfo=None)
                if not (today - timedelta(days=1) <= start <= horizon):
                    continue
                venue, addr, city = _place(node)
                title = clean_text(node.get("name") or "")
                desc = clean_text(HTMLParser(node.get("description") or "").text())
                img = node.get("image")
                if isinstance(img, list):
                    img = img[0] if img else ""
                events.append(Event(
                    source_id="",
                    title=title,
                    start=start,
                    end=parse_dt(node.get("endDate")).replace(tzinfo=None) if node.get("endDate") else None,
                    all_day=True,
                    venue=venue,
                    address=", ".join(p for p in (addr, city) if p),
                    city=city,
                    url=url,
                    description=desc,
                    image=clean_text(str(img or "")),
                    category=infer_category(title, desc, "community"),
                ))
            if fresh == 0:
                break

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events")

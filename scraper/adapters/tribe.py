"""WordPress "The Events Calendar" (Modern Tribe) REST API adapter.

A large share of chamber / tourism / small-city sites run WordPress with this
plugin, which exposes a clean JSON API at /wp-json/tribe/events/v1/events.
When it's present this is by far the most reliable source of structured data.
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlparse

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

MONTHS_AHEAD = 6
PER_PAGE = 50
MAX_PAGES = 12


def _api_root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/wp-json/tribe/events/v1/events"


def _to_event(node: dict) -> Event | None:
    start = parse_dt(node.get("start_date") or node.get("utc_start_date"))
    if not start:
        return None
    venue = node.get("venue") or {}
    if isinstance(venue, list):
        venue = venue[0] if venue else {}
    if not isinstance(venue, dict):
        venue = {}
    cats = node.get("categories") or []
    if isinstance(cats, dict):
        cats = list(cats.values())
    cat_names = ", ".join(c.get("name", "") for c in cats if isinstance(c, dict))
    cost = clean_text(node.get("cost") or "")
    if node.get("cost_details", {}).get("values") == ["0"]:
        cost = "Free"
    img = node.get("image") or {}
    return Event(
        source_id="",
        title=clean_text(node.get("title") or ""),
        start=start,
        end=parse_dt(node.get("end_date")),
        all_day=bool(node.get("all_day")),
        venue=clean_text(venue.get("venue") or ""),
        address=clean_text(", ".join(p for p in [
            venue.get("address"), venue.get("city"), venue.get("state")] if p)),
        city=clean_text(venue.get("city") or ""),
        url=clean_text(node.get("url") or ""),
        description=clean_text(_strip_html(node.get("description") or "")),
        price=cost,
        image=clean_text((img.get("url") if isinstance(img, dict) else img) or ""),
        category=_map_category(cat_names),
    )


def _strip_html(s: str) -> str:
    from selectolax.parser import HTMLParser
    return HTMLParser(s).text() if s else ""


def _map_category(raw: str) -> str:
    r = raw.lower()
    table = [
        ("music", "music"), ("concert", "music"), ("live music", "music"),
        ("theat", "arts"), ("art", "arts"), ("film", "arts"), ("gallery", "arts"),
        ("family", "family"), ("kid", "family"), ("children", "family"),
        ("food", "food-drink"), ("beer", "food-drink"), ("wine", "food-drink"), ("dining", "food-drink"),
        ("race", "sports-rec"), ("run", "sports-rec"), ("sport", "sports-rec"), ("outdoor", "sports-rec"),
        ("class", "education"), ("workshop", "education"), ("lecture", "education"), ("seminar", "education"),
        ("holiday", "holiday"), ("christmas", "holiday"), ("halloween", "holiday"),
        ("market", "community"), ("festival", "community"), ("fair", "community"), ("fundrais", "community"),
    ]
    for needle, cat in table:
        if needle in r:
            return cat
    return "other"


class TribeEventsAdapter:
    name = "tribe"

    def scrape(self, source: Source) -> AdapterResult:
        api = _api_root(source.url)
        start = date.today()
        end = start + timedelta(days=30 * MONTHS_AHEAD)
        events: list[Event] = []
        page = 1
        while page <= MAX_PAGES:
            params = {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "per_page": PER_PAGE,
                "page": page,
            }
            r = fetch.get(api, params=params)
            if r is None:
                break
            try:
                payload = r.json()
            except ValueError:
                return AdapterResult(self.name, ok=False, detail="not a Tribe site")
            batch = payload.get("events", [])
            if not batch:
                break
            for node in batch:
                ev = _to_event(node)
                if ev:
                    events.append(ev)
            if len(batch) < PER_PAGE:
                break
            page += 1

        if page == 1 and not events:
            return AdapterResult(self.name, ok=False, detail="no Tribe API / no events")
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events), detail=f"{len(events)} events")

"""Squarespace events collection (e.g. Grand Marais Playhouse /calendar).

Any Squarespace page answers `?format=json`. For an events collection
(`collection.typeName` "events", "events-stacked", ...) the payload carries `upcoming` (and
`past`) item lists:

    {title, fullUrl, startDate, endDate (epoch ms, UTC), excerpt,
     location {addressTitle, addressLine1, addressLine2}, assetUrl}

`events_url` must be the events collection page itself; a regular page or
a booking form (Fitger's /events-1) has no `upcoming` list. Times are
converted from UTC to America/Chicago wall time. Pagination: Squarespace
returns up to ~20 upcoming items and a `pagination.nextPageUrl` to follow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from models import Event, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

LOCAL = ZoneInfo("America/Chicago")
MAX_PAGES = 5


def _local(ms) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(LOCAL).replace(tzinfo=None, second=0, microsecond=0)


class SquarespaceAdapter:
    name = "squarespace"

    def scrape(self, source: Source) -> AdapterResult:
        url = source.target.split("?")[0]
        items, pages, is_events = [], 0, False
        next_url = url + "?format=json"
        while next_url and pages < MAX_PAGES:
            r = fetch.get(next_url)
            if r is None:
                break
            try:
                data = r.json()
            except ValueError:
                return AdapterResult(self.name, detail="not a Squarespace page")
            pages += 1
            is_events = is_events or str((data.get("collection") or {}).get("typeName") or "").startswith("events")
            items += data.get("upcoming") or []
            nxt = (data.get("pagination") or {}).get("nextPageUrl")
            next_url = urljoin(url, nxt) + ("&" if "?" in nxt else "?") + "format=json" if nxt else None
        if not is_events:
            return AdapterResult(self.name, detail="page is not an events collection")
        events: list[Event] = []
        for it in items:
            start = _local(it.get("startDate"))
            if not start:
                continue
            loc = it.get("location") or {}
            events.append(Event(
                source_id="",
                title=clean_text(it.get("title") or ""),
                start=start,
                end=_local(it.get("endDate")),
                venue=clean_text(loc.get("addressTitle") or ""),
                address=clean_text(" ".join(x for x in (loc.get("addressLine1"), loc.get("addressLine2")) if x)),
                url=urljoin(url, it.get("fullUrl") or ""),
                description=clean_text(it.get("excerpt") or ""),
                image=it.get("assetUrl") or "",
                category="other",
            ))
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} upcoming events")

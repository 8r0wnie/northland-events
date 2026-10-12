"""evvnt "Discovery" community calendars (TownNews/BLOX newspapers, e.g. Mesabi Tribune).

Newspaper "Local Events" pages embed evvnt's discovery plugin, which reads a
public JSON endpoint per publisher:

    GET https://discovery.evvnt.com/api/publisher/<id>/home_page_events
        ?hitsPerPage=50&multipleEventInstances=true&page=N&publisher_id=<id>
    -> {"rawEvents": [{title, start_time "2026-10-16T20:00:00-05:00", end_time,
        summary, description, venue {name, address_1, town, ...}, category_name,
        source_broadcast_url, images, organiser_name}], ...}

Listings are submitted by organizers (often the same events they post on
Facebook), so this is one of the best legitimate routes to Facebook-only
events. `events_url: evvnt:<publisher id>`; the id is in the page's network
calls (`/api/publisher/<id>/home_page_events`). Times carry an offset and go
through `parse_dt`. Venues outside DMA_BOX (by lat/lon) are dropped;
state comes from the ZIP (the venue has no state field). Category names map
via `infer_category`.
"""
from __future__ import annotations

from models import Event, clean_text, parse_dt
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

API = ("https://discovery.evvnt.com/api/publisher/{pid}/home_page_events"
       "?hitsPerPage=50&multipleEventInstances=true&page={n}&publisher_id={pid}")
MAX_PAGES = 20
STATES = {"MN", "WI", "MI", "MINNESOTA", "WISCONSIN", "MICHIGAN"}
_STATE_CODE = {"MINNESOTA": "MN", "WISCONSIN": "WI", "MICHIGAN": "MI"}


# Rough Duluth-DMA box (lat/lon of the venue). Publishers like the Mesabi
# Tribune also list Brainerd-area towns (Walker, Crosby) that are outside it.
DMA_BOX = (45.7, 49.1, -94.4, -88.0)   # min lat, max lat, min lon, max lon


def _in_dma(v: dict) -> bool:
    lat, lon = v.get("latitude"), v.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return True   # no coordinates: keep, the publisher is regional
    a, b, c, d = DMA_BOX
    return a <= lat <= b and c <= lon <= d


def _zip_state(z) -> str:
    z = str(z or "")[:3]
    if not z.isdigit():
        return ""
    n = int(z)
    return "MN" if 550 <= n <= 567 else "WI" if 530 <= n <= 549 else "MI" if 480 <= n <= 499 else ""


# venues often have no ZIP; the source's default state (MN) would mislabel
# the Wisconsin/Michigan side of the DMA
_TOWN_STATE = {t: "WI" for t in (
    "superior", "ashland", "hayward", "bayfield", "washburn", "spooner", "hurley",
    "solon springs", "cable", "siren", "iron river", "port wing", "cornucopia",
    "mellen", "drummond", "poplar", "lake nebagamon", "brule", "maple", "mercer",
    "minocqua", "webster", "grantsburg", "shell lake", "barnes", "gordon")}
_TOWN_STATE.update({t: "MI" for t in ("ironwood", "bessemer", "wakefield", "watersmeet", "marenisco")})


def _publisher(source: Source) -> str | None:
    ev = source.events_url or ""
    if ev.lower().startswith("evvnt:"):
        return ev.split(":", 1)[1].strip() or None
    return None


class EvvntAdapter:
    name = "evvnt"

    def scrape(self, source: Source) -> AdapterResult:
        pid = _publisher(source)
        if not pid:
            return AdapterResult(self.name, detail="no evvnt publisher id (events_url: evvnt:<id>)")
        rows, seen = [], set()
        for n in range(MAX_PAGES):
            r = fetch.get(API.format(pid=pid, n=n), headers={"Accept": "application/json"})
            try:
                batch = (r.json() if r is not None else {}).get("rawEvents") or []
            except ValueError:
                break
            for e in batch:
                key = (e.get("source_id"), e.get("start_time"))
                if key not in seen:
                    seen.add(key)
                    rows.append(e)
            if len(batch) < 50:
                break
        events: list[Event] = []
        for e in rows:
            v = e.get("venue") if isinstance(e.get("venue"), dict) else {}
            if not _in_dma(v) or (v.get("country") and v.get("country") != "United States"):
                continue   # outside the DMA box, or across the border (Fort Frances, ON)
            town = clean_text(v.get("town") or "")
            region = (_zip_state(v.get("post_code")) or _TOWN_STATE.get(town.lower())
                      or clean_text(v.get("region") or "").upper())
            start = parse_dt(e.get("start_time") or e.get("start_date"))
            if not start:
                continue
            desc = clean_text(e.get("description") or e.get("summary") or "")
            events.append(Event(
                source_id="", title=clean_text(e.get("title") or ""), start=start,
                end=parse_dt(e.get("end_time")),
                all_day=not e.get("start_time"),
                venue=clean_text(v.get("name") or ""),
                address=clean_text(" ".join(x for x in (v.get("address_1"), v.get("address_2")) if x)),
                city=clean_text(v.get("town") or ""),
                state=_STATE_CODE.get(region, region if len(region) == 2 else ""),
                url=e.get("source_broadcast_url") or source.url,
                description=desc,
                category="other",
            ))
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events (publisher {pid}, {len(rows)} listings)")

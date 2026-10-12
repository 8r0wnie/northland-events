"""Explore Minnesota (state tourism) — statewide event search, filtered to the DMA.

exploreminnesota.com lists events through a Cludo search proxy the page calls
for itself:

    POST /cludosearch/api   {"query": "*", "perPage": N, "page": P,
                             "filters": {"env": ["live"], "content_type": ["event"],
                                         "date": ["allDates_iso_date", "<today>", ""]}, ...}
    -> {"TotalDocument": n, "TypedDocuments": [{"Fields": {Title, Url, body,
        listing_location, Cities, Category, allDates_iso_date, ...}}]}

Plain requests get HTTP 403, so — like `tribehtml` — the adapter loads the
public events page in a headless browser and runs the same POST from inside it,
once per run (the site 403s repeat search calls from one page load; we don't
work around that).
There's no region field, so events are kept only when `listing_location` (or
one of `Cities`) is a town in DMA_TOWNS. Every listed date is given; runs of
consecutive dates become one multi-day event. No time of day → all-day.
Pin-only (browser).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from models import Event, clean_text, parse_dt
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

PAGE_URL = "https://www.exploreminnesota.com/things-to-do/festivals-events"
PER_PAGE = 2000   # one call returns the whole statewide list (~1,000)
HORIZON_DAYS = 200

# Minnesota towns inside the Duluth DMA (Explore MN `listing_location` values).
DMA_TOWNS = {t.lower() for t in [
    "Duluth", "Hermantown", "Proctor", "Two Harbors", "Knife River", "Silver Bay",
    "Beaver Bay", "Finland", "Isabella", "Schroeder", "Tofte", "Lutsen",
    "Grand Marais", "Hovland", "Grand Portage", "Ely", "Tower", "Soudan", "Babbitt",
    "Virginia", "Eveleth", "Gilbert", "Mountain Iron", "Biwabik", "Aurora",
    "Hoyt Lakes", "Hibbing", "Chisholm", "Buhl", "Kinney", "Cook", "Orr", "Crane Lake",
    "International Falls", "Ranier", "Kabetogama", "Littlefork", "Grand Rapids",
    "Cohasset", "Deer River", "La Prairie", "Coleraine", "Bovey", "Taconite",
    "Marble", "Calumet", "Nashwauk", "Keewatin", "Bigfork", "Hill City", "Aitkin",
    "McGregor", "Palisade", "Cloquet", "Carlton", "Scanlon", "Thomson", "Esko",
    "Wrenshall", "Moose Lake", "Barnum", "Cromwell", "Kettle River", "Willow River",
    "Sturgeon Lake", "Hinckley", "Finlayson", "Sandstone", "Askov", "Pine City",
    "Rush City", "Floodwood", "Saginaw", "Cotton", "Brimson", "Meadowlands",
    "Side Lake", "Embarrass", "Lake Vermilion",
]}

_CATEGORY = [
    ("Holiday", "holiday"), ("Children", "family"), ("Sporting", "sports-rec"),
    ("Birding", "sports-rec"), ("Lectures", "education"), ("Science", "education"),
    ("Arts & Culture", "arts"),
]

_JS = """async (body) => {
  const r = await fetch('/cludosearch/api', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  return r.ok ? await r.json() : {error: r.status};
}"""


def _field(doc: dict, name: str) -> str:
    f = (doc.get("Fields") or {}).get(name) or {}
    return clean_text(str(f.get("Value") or ""))


def _values(doc: dict, name: str) -> list[str]:
    f = (doc.get("Fields") or {}).get(name) or {}
    vals = f.get("Values") or ([f["Value"]] if f.get("Value") else [])
    return [clean_text(str(v)) for v in vals]


def _runs(days: list[date]) -> list[tuple[date, date]]:
    """[d1, d2, d3, d7] -> [(d1, d3), (d7, d7)]"""
    out: list[tuple[date, date]] = []
    for d in sorted(set(days)):
        if out and d - out[-1][1] == timedelta(days=1):
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


class ExploreMnAdapter:
    name = "exploremn"

    def _search(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []
        today = date.today().isoformat()
        docs: list[dict] = []
        fetch._throttle(PAGE_URL)
        with fetch.playwright_session() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=fetch.USER_AGENT, locale="en-US")
            try:
                # One search call per run. The site answers only the first
                # couple of search calls per page load (403 after), so we make
                # a single request with a page size covering everything,
                # before the page fires its own, and never retry around it.
                page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
                body = {"query": "*", "perPage": PER_PAGE, "page": 1,
                        "enableFacetFiltering": True,
                        "filters": {"env": ["live"], "search_api_language": ["en"],
                                    "content_type": ["event"],
                                    "date": ["allDates_iso_date", today, ""]},
                        "notFilters": {}, "facets": {},
                        "sort": {"sort_next_start_date": "asc"}}
                data = page.evaluate(_JS, body)
                docs = (data or {}).get("TypedDocuments") or []
            finally:
                browser.close()
        return docs

    def scrape(self, source: Source) -> AdapterResult:
        try:
            docs = self._search()
        except Exception as exc:  # noqa: BLE001 — browser failures are non-fatal
            return AdapterResult(self.name, detail=f"browser error: {str(exc)[:60]}")
        if not docs:
            return AdapterResult(self.name, detail="no search results")
        today = date.today()
        horizon = today + timedelta(days=HORIZON_DAYS)
        events: list[Event] = []
        kept = 0
        for doc in docs:
            town = _field(doc, "listing_location")
            towns = [town] + [c for c in _values(doc, "Cities") if c]
            local = next((t for t in towns if t.lower() in DMA_TOWNS), None)
            if not local:
                continue
            kept += 1
            days = [d.date() for d in (parse_dt(v) for v in _values(doc, "allDates_iso_date")) if d]
            days = [d for d in days if today - timedelta(days=1) <= d <= horizon]
            cats = _field(doc, "Category")
            category = next((c for k, c in _CATEGORY if k in cats), "community")
            for first, last in _runs(days):
                events.append(Event(
                    source_id="", title=_field(doc, "Title"),
                    start=datetime.combine(first, datetime.min.time()),
                    end=datetime.combine(last, datetime.min.time()) if last != first else None,
                    all_day=True, city=local, state="MN", area=local,
                    url=_field(doc, "Url"), description=_field(doc, "body")[:600],
                    category=category,
                ))
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events from {kept}/{len(docs)} DMA listings")

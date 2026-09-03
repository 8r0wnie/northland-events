"""Simpleview DTN adapter (CVB / tourism sites, e.g. Bayfield WI).

Simpleview event list pages load data from a WAF-protected REST endpoint
(`/includes/rest_v2/plugins_events_events_by_date/find/`) that rejects non-browser
clients. So this adapter drives a headless browser: it loads the events page
(and `?skip=N` pages) and captures the JSON the page fetches for itself.

Each result doc: title, startDate/endDate (ISO), location, url (`/event/<slug>/<id>/`),
media_raw (images), recurrence text.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source

PAGE_SIZE = 24
MAX_PAGES = 12
HORIZON_DAYS = 120
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}


def _events_from_doc(doc: dict, base: str) -> list[Event]:
    """Simpleview returns one doc per event with the *series* startDate; recurrence
    is not expanded. Emit one-time events directly, and expand simple
    'weekly on <Day>' patterns; skip anything else stuck in the past."""
    title = clean_text(doc.get("title") or "")
    start = parse_dt(doc.get("startDate"))
    end = parse_dt(doc.get("endDate"))
    if not title or not start:
        return []
    start = start.replace(tzinfo=None)
    end = end.replace(tzinfo=None) if end else None

    media = doc.get("media_raw") or []
    img = media[0].get("mediaurl", "") if media and isinstance(media[0], dict) else ""
    url = doc.get("url") or ""
    if url.startswith("/"):
        url = base.rstrip("/") + url
    recur = clean_text(doc.get("recurrence") or "")
    loc = clean_text(doc.get("location") or "")
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = today + timedelta(days=HORIZON_DAYS)

    # Simpleview marks date-only events with a midnight-Central start
    # (05:00Z in CDT, 06:00Z in CST).
    date_only = start.hour in (5, 6) and start.minute == 0

    def make(dt, dtend):
        if date_only:
            dt = dt.replace(hour=0, minute=0)
        return Event(source_id="", title=title, start=dt, end=dtend, venue=loc,
                     address=loc, url=url, description=recur, image=clean_text(img),
                     category="community", all_day=date_only)

    if start >= today - timedelta(days=1):
        return [make(start, end)]

    m = re.search(r"weekly on (\w+)", recur, re.I)
    if m and m.group(1).lower() in _WEEKDAYS:
        wd = _WEEKDAYS[m.group(1).lower()]
        cur = today + timedelta((wd - today.weekday()) % 7)
        cur = cur.replace(hour=start.hour, minute=start.minute)
        stop = min(horizon, end) if end else horizon
        out = []
        while cur <= stop and len(out) < 20:
            out.append(make(cur, None))
            cur += timedelta(days=7)
        return out
    return []


class SimpleviewAdapter:
    name = "simpleview"

    def scrape(self, source: Source) -> AdapterResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AdapterResult(self.name, ok=False, detail="playwright unavailable")

        events_url = source.events_url or (source.url.rstrip("/") + "/events/")
        from urllib.parse import urlparse
        base = f"{urlparse(events_url).scheme}://{urlparse(events_url).netloc}"

        captured: list[dict] = []
        UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=UA, locale="en-US")

                def on_response(r):
                    if "events_by_date/find" in r.url:
                        try:
                            captured.append(r.json())
                        except Exception:
                            pass

                page.on("response", on_response)

                page.goto(events_url, wait_until="networkidle", timeout=40000)
                page.wait_for_timeout(2500)

                count = 0
                for payload in captured:
                    docs = payload.get("docs", {})
                    count = max(count, docs.get("count", 0) if isinstance(docs, dict) else 0)

                skip = PAGE_SIZE
                pages = 1
                while skip < count and pages < MAX_PAGES:
                    sep = "&" if "?" in events_url else "?"
                    page.goto(f"{events_url}{sep}skip={skip}", wait_until="networkidle", timeout=40000)
                    page.wait_for_timeout(1500)
                    skip += PAGE_SIZE
                    pages += 1

                browser.close()
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(self.name, ok=False, detail=f"browser error: {str(exc)[:60]}")

        seen: set[str] = set()
        events: list[Event] = []
        for payload in captured:
            docs = payload.get("docs", {})
            rows = docs.get("docs", []) if isinstance(docs, dict) else []
            for doc in rows:
                key = str(doc.get("_id") or doc.get("recid") or "")
                if key and key in seen:
                    continue
                seen.add(key)
                events.extend(_events_from_doc(doc, base))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events ({len(captured)} pages)")

"""'Events Calendar' Wix app (inffuse / eventscalendar.co).

Wix venues embed this widget in an iframe; it aggregates the venue's Google
Calendar and/or Eventbrite org and serves them from
`broker.eventscalendar.co/api/{google,eventbrite}/next`. The widget carries the
user/project/calendar ids, so we load the venue's events page in a headless
browser and capture the broker responses.

Each event: title, description, location, location_address, image,
start / end (epoch ms), start_time (ISO).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from models import Event, clean_text, infer_category
from registry import Source
from adapters.base import AdapterResult, tag_from_source

HORIZON_DAYS = 160
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class EventsCalendarAdapter:
    name = "eventscalendar"

    def scrape(self, source: Source) -> AdapterResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AdapterResult(self.name, ok=False, detail="playwright unavailable")

        events_url = source.events_url or (source.url.rstrip("/") + "/events")
        batches: list[list] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=UA, locale="en-US")

                def on_response(r):
                    if "broker.eventscalendar.co/api/" in r.url and "/next" in r.url:
                        try:
                            body = r.json()
                            if isinstance(body, dict) and body.get("events"):
                                batches.append(body["events"])
                        except Exception:
                            pass

                page.on("response", on_response)
                page.goto(events_url, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(6000)
                for _ in range(3):
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(1500)
                browser.close()
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(self.name, ok=False, detail=f"browser error: {str(exc)[:60]}")

        if not batches:
            return AdapterResult(self.name, ok=False, detail="no eventscalendar widget data")

        now = datetime.now()
        horizon = now + timedelta(days=HORIZON_DAYS)
        seen: set[str] = set()
        events: list[Event] = []
        for batch in batches:
            for node in batch:
                ts = node.get("start")
                if not ts:
                    continue
                start = datetime.fromtimestamp(ts / 1000)
                if not (now - timedelta(days=1) <= start <= horizon):
                    continue
                eid = str(node.get("id") or "")
                if eid and eid in seen:
                    continue
                seen.add(eid)
                end = datetime.fromtimestamp(node["end"] / 1000) if node.get("end") else None
                if end and end <= start:
                    end = None
                title = clean_text(node.get("title") or "")
                loc = clean_text(node.get("location") or "")
                desc = clean_text(node.get("description") or "")
                events.append(Event(
                    source_id="",
                    title=title,
                    start=start,
                    end=end,
                    venue=loc,
                    address=clean_text(node.get("location_address") or loc),
                    url=clean_text(node.get("url") or events_url),
                    description=desc,
                    image=clean_text(node.get("image") or ""),
                    category=infer_category(title, desc, "music"),
                ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events ({len(batches)} feeds)")

"""OvationTix / AudienceView adapter (e.g. NorShor Theatre / Duluth Playhouse).

The venue's events page embeds an OvationTix widget per production; the widget
fetches `api.ovationtix.com/public/events/client(<id>)/production(<pid>)` for
each. Those endpoints reject bare requests but load fine inside the page, so
this adapter drives a headless browser and captures the JSON.

Each `performances[]` entry: performanceStart (epoch ms), productionName,
performanceSuperTitle / performanceSubTitle (venue / series).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from models import Event, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source

HORIZON_DAYS = 320
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class OvationTixAdapter:
    name = "ovationtix"

    def scrape(self, source: Source) -> AdapterResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AdapterResult(self.name, ok=False, detail="playwright unavailable")

        events_url = source.events_url or (source.url.rstrip("/") + "/events")
        payloads: list[dict] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=UA, locale="en-US")

                def on_response(r):
                    if "api.ovationtix.com" in r.url and "/production(" in r.url:
                        try:
                            payloads.append(r.json())
                        except Exception:
                            pass

                page.on("response", on_response)
                page.goto(events_url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(4000)
                # some sites paginate productions; nudge a scroll
                for _ in range(3):
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(1500)
                browser.close()
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(self.name, ok=False, detail=f"browser error: {str(exc)[:60]}")

        if not payloads:
            return AdapterResult(self.name, ok=False, detail="no OvationTix data on page")

        now = datetime.now()
        horizon = now + timedelta(days=HORIZON_DAYS)
        seen: set[tuple] = set()
        events: list[Event] = []
        for payload in payloads:
            for perf in payload.get("performances", []) or []:
                ts = perf.get("performanceStart")
                if not ts:
                    continue
                start = datetime.fromtimestamp(ts / 1000)
                if not (now - timedelta(days=1) <= start <= horizon):
                    continue
                title = clean_text(perf.get("productionName") or "")
                if not title:
                    continue
                key = (title.lower(), start.date())
                if key in seen:
                    continue
                seen.add(key)
                sub = clean_text(perf.get("performanceSubTitle") or "")
                venue = sub[3:].strip() if sub.lower().startswith("at ") else (sub or source.name)
                events.append(Event(
                    source_id="",
                    title=title,
                    start=start,
                    venue=venue,
                    url=events_url,
                    description=clean_text(perf.get("performanceSuperTitle") or ""),
                    category="arts",
                ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} performances")

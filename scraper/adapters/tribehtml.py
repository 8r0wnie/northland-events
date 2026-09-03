"""The Events Calendar via its RSS feed, fetched through a headless browser.

For TEC sites whose wp-json REST API and .ics endpoints are behind a bot wall
(Perfect Duluth Day / Cloudflare), the plain events feed at
`<events_url>/feed/?paged=N` stays reachable. We load the site once in a real
browser for challenge clearance, then fetch the feed pages from inside the page.

Each <item>: <title> event name, <link> `/the-event/<slug>/<YYYY-MM-DD>/`,
<pubDate> event start datetime, <description> blurb.
"""
from __future__ import annotations

import html as _html
import re
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _CENTRAL = ZoneInfo("America/Chicago")
except Exception:  # noqa: BLE001
    _CENTRAL = None

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source

MAX_PAGES = 16
HORIZON_DAYS = 150
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_ITEM = re.compile(r"<item>(.*?)</item>", re.S | re.I)
_TAG = re.compile(r"<(title|link|pubDate|description)>(.*?)</\1>", re.S | re.I)
_URL_DATE = re.compile(r"/(\d{4}-\d{2}-\d{2})/?$")


def _cdata(s: str) -> str:
    m = re.search(r"<!\[CDATA\[(.*?)\]\]>", s, re.S)
    return _html.unescape((m.group(1) if m else s)).strip()


class TribeHtmlAdapter:
    name = "tribehtml"

    def scrape(self, source: Source) -> AdapterResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AdapterResult(self.name, ok=False, detail="playwright unavailable")

        root = (source.events_url or source.url).rstrip("/")
        feed = f"{root}/feed/"
        site = f"{root.split('/', 3)[0]}//{root.split('/', 3)[2]}"

        pages: list[str] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=UA, locale="en-US")
                page.goto(site, wait_until="domcontentloaded", timeout=35000)
                page.wait_for_timeout(2000)
                for n in range(1, MAX_PAGES + 1):
                    try:
                        txt = page.evaluate(
                            "async (u) => { const r = await fetch(u); return r.ok ? await r.text() : ''; }",
                            f"{feed}?paged={n}")
                    except Exception:
                        break
                    if not txt or "<item>" not in txt:
                        break
                    pages.append(txt)
                    # stop once this page's newest item is past the horizon
                    last_dates = re.findall(r"/the-event/[^/]+/(\d{4}-\d{2}-\d{2})/", txt)
                    if last_dates and max(last_dates) > (date.today() + timedelta(days=HORIZON_DAYS)).isoformat():
                        break
                browser.close()
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(self.name, ok=False, detail=f"browser error: {str(exc)[:60]}")

        if not pages:
            return AdapterResult(self.name, ok=False, detail="feed unreachable")

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        horizon = today + timedelta(days=HORIZON_DAYS)
        seen: set[str] = set()
        events: list[Event] = []
        for xml in pages:
            for block in _ITEM.findall(xml):
                f = {k.lower(): _cdata(v) for k, v in _TAG.findall(block)}
                link = f.get("link", "")
                start = parse_dt(f.get("pubdate", ""))
                if not start:
                    m = _URL_DATE.search(link)
                    start = parse_dt(m.group(1)) if m else None
                if not start:
                    continue
                if start.tzinfo is not None:
                    start = (start.astimezone(_CENTRAL) if _CENTRAL else start).replace(tzinfo=None)
                if not (today - timedelta(days=1) <= start <= horizon):
                    continue
                key = f"{link}"
                if key in seen:
                    continue
                seen.add(key)
                events.append(Event(
                    source_id="",
                    title=clean_text(f.get("title", "")),
                    start=start,
                    all_day=start.hour == 0 and start.minute == 0,
                    url=link,
                    description=clean_text(re.sub(r"<[^>]+>", " ", f.get("description", ""))),
                    category="community",
                ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events ({len(pages)} feed pages)")

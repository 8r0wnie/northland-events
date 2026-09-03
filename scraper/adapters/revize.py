"""Revize municipal-CMS adapter.

Revize sites render their calendar with FullCalendar, fed by a public JSON
handler on the site's own domain:

    /_assets_/plugins/revizeCalendar/calendar_data_handler.php
        ?webspace=<slug>&relative_revize_url=//webgen1.revize.com&protocol=https:

It returns every event (past and future) as
    {title, start, end, location, url, desc, image, rrule, duration, color}
with `start`/`end` as naive local ISO timestamps and an optional iCal `rrule`
string for recurring items.

The `<slug>` is discovered from `webspace=<slug>` in the site's HTML.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from dateutil import rrule as _rr
from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

HANDLER = "/_assets_/plugins/revizeCalendar/calendar_data_handler.php"
HORIZON_DAYS = 210
_WEBSPACE = re.compile(r"webspace=([A-Za-z0-9_-]+)")


def _root(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _webspace(source: Source) -> str | None:
    for page in (source.url, source.url.rstrip("/") + "/calendar.php", source.events_url):
        if not page:
            continue
        html = fetch.get_text(page)
        if html:
            m = _WEBSPACE.search(html)
            if m:
                return m.group(1)
    return None


def _img_src(raw: str) -> str:
    if not raw or "<img" not in raw:
        return clean_text(raw)
    el = HTMLParser(raw).css_first("img")
    src = el.attributes.get("src", "") if el else ""
    return "" if "placeholder" in src else src


_SAFE_FREQ = re.compile(r"FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)", re.I)
_RRULE_LINE = re.compile(r"RRULE:([^\n\r]+)", re.I)


def _occurrences(node: dict, start: datetime, end: datetime | None,
                 window_start: datetime, window_end: datetime) -> list[tuple[datetime, datetime | None]]:
    """Expand a recurring event within [window_start, window_end].

    Only DAILY/WEEKLY/MONTHLY/YEARLY rules are expanded, and only from a start
    clamped to the window — an unbounded SECONDLY/MINUTELY rule (or a rule whose
    DTSTART is years in the past) would make dateutil iterate effectively
    forever.
    """
    in_window = window_start <= start <= window_end
    rule = node.get("rrule") or ""
    rline = _RRULE_LINE.search(rule)
    if not rline or not _SAFE_FREQ.search(rline.group(1)):
        return [(start, end)] if in_window else []

    span = (end - start) if end else timedelta(0)
    clamped = max(start, window_start - timedelta(days=1))
    try:
        rs = _rr.rrulestr("RRULE:" + rline.group(1), dtstart=clamped)
        out = []
        for dt in rs:
            if dt > window_end:
                break
            if dt >= window_start:
                out.append((dt, dt + span if end else None))
            if len(out) >= 60:
                break
        return out
    except (ValueError, TypeError, OverflowError):
        return [(start, end)] if in_window else []


class RevizeAdapter:
    name = "revize"

    def scrape(self, source: Source) -> AdapterResult:
        slug = _webspace(source)
        if not slug:
            return AdapterResult(self.name, ok=False, detail="no Revize webspace found")
        root = _root(source.url)
        url = (f"{root}{HANDLER}?webspace={slug}"
               f"&relative_revize_url=//webgen1.revize.com&protocol=https:")
        r = fetch.get(url)
        if r is None:
            return AdapterResult(self.name, ok=False, detail="handler request failed")
        try:
            rows = r.json()
        except ValueError:
            return AdapterResult(self.name, ok=False, detail="handler did not return JSON")

        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        horizon = now + timedelta(days=HORIZON_DAYS)
        events: list[Event] = []
        for node in rows:
            start = parse_dt(node.get("start"))
            if not start:
                continue
            end = parse_dt(node.get("end"))
            for occ_start, occ_end in _occurrences(node, start, end, now, horizon):
                events.append(Event(
                    source_id="",
                    title=clean_text(node.get("title") or ""),
                    start=occ_start,
                    end=occ_end,
                    all_day=(occ_start.hour == 0 and occ_start.minute == 0 and not end),
                    venue=clean_text(node.get("location") or ""),
                    address=clean_text(node.get("location") or ""),
                    url=clean_text(node.get("url") or "") or f"{root}/calendar.php",
                    description=clean_text(HTMLParser(node.get("desc") or "").text()),
                    image=_img_src(node.get("image") or ""),
                    category="community",
                ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events (webspace {slug})")

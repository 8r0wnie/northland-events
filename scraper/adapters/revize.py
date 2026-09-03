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
from datetime import date, datetime, timedelta

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


def _old_xml_events(root: str, now: datetime, horizon: datetime) -> list[Event]:
    """Older Revize calendars serve per-month XML at
    /calendar_app/db/calendar_1_activemonthsdata_YYYY-MM.xml with a pre-expanded
    <dates> list (MM-DD-YYYY, comma separated)."""
    from selectolax.parser import HTMLParser as _HP
    seen: set[str] = set()
    out: list[Event] = []
    month = date(now.year, now.month, 1)
    for i in range(7):
        url = f"{root}/calendar_app/db/calendar_1_activemonthsdata_{month:%Y-%m}.xml"
        xml = fetch.get_text(url)
        month = date(month.year + (month.month // 12), (month.month % 12) + 1, 1)
        if xml is None and i == 0:
            break  # not a legacy Revize calendar site
        if not xml or "<event" not in xml:
            continue
        for m in re.finditer(r"<event\b[^>]*>(.*?)</event>", xml, re.S):
            block = m.group(1)

            def tag(name: str) -> str:
                mm = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S)
                if not mm:
                    return ""
                val = mm.group(1)
                cd = re.match(r"\s*<!\[CDATA\[(.*?)\]\]>\s*$", val, re.S)
                return clean_text(cd.group(1) if cd else val)

            eid = re.search(r'id="(\d+)"', m.group(0))
            eid = eid.group(1) if eid else tag("name")
            title = tag("name")
            if not title:
                continue
            tstart = tag("time_begin")
            detail = tag("detail") or tag("summary")
            dates = tag("dates") or ""
            day_list = [d.strip() for d in dates.split(",") if d.strip()] or [tag("date_begin")]
            for d in day_list:
                dt = parse_dt(f"{d} {tstart}".strip())
                if not dt or not (now <= dt <= horizon):
                    continue
                key = f"{eid}|{dt.date()}"
                if key in seen:
                    continue
                seen.add(key)
                out.append(Event(
                    source_id="", title=title, start=dt,
                    all_day=not tstart,
                    description=clean_text(_HP(detail).text() if "<" in detail else detail),
                    url=f"{root}/calendar.php", category="community",
                ))
    return out


class RevizeAdapter:
    name = "revize"

    def scrape(self, source: Source) -> AdapterResult:
        slug = _webspace(source)
        root = _root(source.url)
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        horizon = now + timedelta(days=HORIZON_DAYS)

        rows = None
        if slug:
            url = (f"{root}{HANDLER}?webspace={slug}"
                   f"&relative_revize_url=//webgen1.revize.com&protocol=https:")
            r = fetch.get(url)
            if r is not None:
                try:
                    rows = r.json()
                except ValueError:
                    rows = None

        if rows is None:
            # Fall back to the older per-month XML calendar.
            events = tag_from_source(_old_xml_events(root, now, horizon), source)
            if events:
                return AdapterResult(self.name, events=events, ok=True,
                                     detail=f"{len(events)} events (old XML)")
            return AdapterResult(self.name, ok=False, detail="no Revize calendar data")

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

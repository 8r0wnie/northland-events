"""Generic iCal / .ics feed adapter.

Discovers a calendar feed from the events page via:
  * <link rel="alternate" type="text/calendar">
  * anchors / buttons whose href ends in .ics or contains ?ical= / outlook-ical
  * the WordPress Events Calendar convention  <events_url>?ical=1
then parses it with the icalendar library.
"""
from __future__ import annotations

import re
from datetime import datetime, date, time, timedelta
from urllib.parse import urljoin

from dateutil import rrule as _rr
from icalendar import Calendar
from selectolax.parser import HTMLParser

from models import Event, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch


def _gcal_id(cid: str) -> str:
    """Google embeds pass the calendar id raw ("x%40group.calendar.google.com")
    or base64-encoded ("Z3JhbmRt..."). Return the URL-encoded raw id."""
    import base64
    from urllib.parse import quote, unquote
    raw = unquote(cid)
    if "@" not in raw:
        try:
            dec = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
            if "@" in dec and dec.isprintable():
                raw = dec
        except (ValueError, UnicodeDecodeError):
            pass
    return quote(raw, safe="")


def _discover(html: str, base: str) -> list[str]:
    tree = HTMLParser(html)
    found: list[str] = []
    for link in tree.css('link[rel="alternate"][type="text/calendar"]'):
        href = link.attributes.get("href")
        if href:
            found.append(urljoin(base, href))
    for a in tree.css("a[href]"):
        raw = a.attributes.get("href") or ""
        href = raw.lower()
        if href.endswith(".ics") or "ical=1" in href or "outlook-ical" in href or "format=ical" in href:
            found.append(urljoin(base, raw.replace("webcal://", "https://")))
    # Google Calendar embeds expose a public .ics per calendar id
    for iframe in tree.css('iframe[src*="google.com/calendar"], iframe[src*="calendar.google.com"]'):
        for cid in re.findall(r"[?&]src=([^&]+)", iframe.attributes.get("src", "")):
            cid = _gcal_id(cid)
            if "holiday" in cid and "group.v.calendar.google.com" in cid:
                continue  # Google's built-in public-holiday overlay
            found.append(f"https://calendar.google.com/calendar/ical/{cid}/public/basic.ics")
    # common conventions
    if "mec-" in html or "modern-events-calendar" in html:
        # WordPress Modern Events Calendar: site-wide feed at /?mec-ical-feed=1
        from urllib.parse import urlparse
        p = urlparse(base)
        found.append(f"{p.scheme}://{p.netloc}/?mec-ical-feed=1")
    found.append(base.rstrip("/") + "/?ical=1")
    # de-dup, keep order
    seen, out = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:10]


_LOCAL_TZ = None


def _dt(value) -> datetime | None:
    """Naive America/Chicago wall time. Aware values (UTC "...Z" or any TZID)
    are converted first; stripping tzinfo alone turned a 7 pm UTC-stamped
    event into 1 am (fixed 2026-10-11)."""
    global _LOCAL_TZ
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            if _LOCAL_TZ is None:
                from zoneinfo import ZoneInfo
                _LOCAL_TZ = ZoneInfo("America/Chicago")
            value = value.astimezone(_LOCAL_TZ)
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return None


HORIZON_DAYS = 200
MAX_PER_SERIES = 60
FREQUENT_SERIES_DAYS = 60   # project rule — see docs/ARCHITECTURE.md "Recurring series"
_SAFE_FREQ = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
_UNTIL = re.compile(r"UNTIL=(\d{8})(T\d{6})?Z?", re.I)


def _expand(comp, start: datetime, end: datetime | None,
            win_start: datetime, win_end: datetime) -> list[tuple]:
    """Occurrences of one VEVENT inside the window (expands RRULE safely)."""
    rule = comp.get("rrule")
    if not rule:
        return [(start, end)] if win_start <= start <= win_end else []
    try:
        rline = rule.to_ical().decode()
    except Exception:  # noqa: BLE001
        return [(start, end)] if win_start <= start <= win_end else []
    if not any(f"FREQ={f}" in rline.upper() for f in _SAFE_FREQ):
        return []  # SECONDLY/MINUTELY/HOURLY rules: unbounded, never useful here
    # UNTIL is usually UTC ("...Z") while DTSTART is naive local — make it naive
    rline = _UNTIL.sub(lambda m: f"UNTIL={m.group(1)}T235959", rline)
    exdates = set()
    ex = comp.get("exdate")
    for block in (ex if isinstance(ex, list) else [ex] if ex else []):
        for d in getattr(block, "dts", []):
            v = _dt(d.dt)
            if v:
                exdates.add(v)
    span = (end - start) if end else None
    try:
        rs = _rr.rrulestr("RRULE:" + rline, dtstart=start)
        # Frequent series (daily/weekly programs: open gym, walking club) show
        # only the next FREQUENT_SERIES_DAYS, rolling forward each run, so they
        # can't swamp one-off events. Monthly/yearly series get the full window.
        frequent = any(f"FREQ={f}" in rline.upper() for f in ("DAILY", "WEEKLY"))
        until = min(win_end, win_start + timedelta(days=FREQUENT_SERIES_DAYS)) if frequent else win_end
        hits = rs.between(win_start, until, inc=True)
    except (ValueError, TypeError, OverflowError):
        return [(start, end)] if win_start <= start <= win_end else []
    out = [(dt, dt + span if span is not None else None) for dt in hits if dt not in exdates]
    return out[:MAX_PER_SERIES]


class IcsAdapter:
    name = "ics"

    def _parse(self, text: str, feed_url: str) -> list[Event]:
        try:
            cal = Calendar.from_ical(text)
        except (ValueError, Exception):  # noqa: BLE001
            return []
        out: list[Event] = []
        now = datetime.now()
        win_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        win_end = win_start + timedelta(days=HORIZON_DAYS)
        comps = list(cal.walk("VEVENT"))
        # instances edited individually carry RECURRENCE-ID; their original
        # slot in the series must not be emitted twice
        overridden = {(str(c.get("uid")), _dt(c.get("recurrence-id").dt))
                      for c in comps if c.get("recurrence-id")}
        for comp in comps:
            if not comp.get("dtstart"):
                continue
            raw_start = comp.get("dtstart").dt
            start = _dt(raw_start)
            if not start:
                continue
            end = _dt(comp.get("dtend").dt) if comp.get("dtend") else None
            all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)
            uid = str(comp.get("uid"))
            occurrences = _expand(comp, start, end, win_start, win_end)
            occurrences = [(s_, e_) for s_, e_ in occurrences if (uid, s_) not in overridden
                           or comp.get("recurrence-id")]
            loc = clean_text(str(comp.get("location") or ""))
            for occ_start, occ_end in occurrences:
                out.append(Event(
                    source_id="",
                    title=clean_text(str(comp.get("summary") or "")),
                    start=occ_start,
                    end=occ_end,
                    venue=loc.split(",")[0] if loc else "",
                    address=loc,
                    url=clean_text(str(comp.get("url") or feed_url)),
                    description=clean_text(str(comp.get("description") or "")),
                    all_day=all_day,
                ))
        return out

    def scrape(self, source: Source) -> AdapterResult:
        target = source.target.replace("webcal://", "https://")
        # events_url may itself be a calendar feed
        low = target.lower()
        if (low.split("?")[0].endswith(".ics") or "ical=1" in low
                or "format=ical" in low or "outlook-ical" in low or "mec-ical-feed" in low):
            text = fetch.get_text(target)
            if text and "BEGIN:VCALENDAR" in text:
                events = tag_from_source(self._parse(text, target), source)
                return AdapterResult(self.name, events=events, ok=bool(events),
                                     detail=f"{len(events)} events from {target}")

        html = fetch.get_text(target)
        if not html:
            return AdapterResult(self.name, ok=False, detail="fetch failed")
        feeds = _discover(html, target)
        gcal = [f for f in feeds if "calendar.google.com" in f]
        if gcal:
            # one Google embed often layers several calendars (city events,
            # parks, library...) — merge them all, as the embed itself does
            events, used = [], 0
            for feed in gcal:
                text = fetch.get_text(feed)
                if text and "BEGIN:VCALENDAR" in text:
                    got = self._parse(text, feed)
                    used += bool(got)
                    events += got
            events = tag_from_source(events, source)
            if events:
                return AdapterResult(self.name, events=events, ok=True,
                                     detail=f"{len(events)} events from {used} Google calendar(s)")
        for feed in feeds:
            if feed in gcal:
                continue
            text = fetch.get_text(feed)
            if not text or "BEGIN:VCALENDAR" not in text:
                continue
            events = tag_from_source(self._parse(text, feed), source)
            if events:
                return AdapterResult(self.name, events=events, ok=True,
                                     detail=f"{len(events)} events from {feed}")
        return AdapterResult(self.name, ok=False, detail="no usable ics feed")

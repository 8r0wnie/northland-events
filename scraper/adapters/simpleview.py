"""Simpleview DTN adapter (CVB / tourism sites, e.g. Bayfield WI).

Simpleview event list pages load data from a WAF-protected REST endpoint
(`/includes/rest_v2/plugins_events_events_by_date/find/`) that rejects non-browser
clients. So this adapter drives a headless browser: it loads the events page
(and `?skip=N` pages) and captures the JSON the page fetches for itself.

Each list doc: title, startDate/endDate (the *series* span, midnight Central as
UTC), `date` (the occurrence the list is showing), location, url
(`/event/<slug>/<id>/`), media_raw, recurrence text, recurType. The list has no
time of day. Each event's own page (plain HTTP, not WAF-gated) embeds
`var data = {...}` (description, admission, recurrence) and `var time = "10:00
AM to 11:00 AM"`, so the adapter reads it once per event.

The series span is never published as one event (that drew a weekly class as
a week-long bar). Recurrence text is expanded into dated occurrences; anything
it can't parse falls back to the occurrence dates the list itself showed.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from dateutil import parser as dtparse

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

PAGE_SIZE = 24
MAX_PAGES = 12
HORIZON_DAYS = 120        # one-time events
SERIES_DAYS = 60          # recurring occurrences (same window as the frequent-series rule)
DAILY_EXPAND_MAX = 14     # a timed daily run up to this long becomes one event a day
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}
_ORDINALS = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3,
             "4th": 4, "fourth": 4, "5th": 5, "fifth": 5, "last": -1}
_CLOCK = r"\d{1,2}(?::\d{2})?\s*[ap]\.?\s*m\.?"
_TIME_RANGE = re.compile(rf"({_CLOCK})(?:\s*(?:to|-|–|—)\s*({_CLOCK}))?", re.I)


def parse_time_range(text: str) -> tuple[Optional[time], Optional[time]]:
    """'10:00 AM to 11:00 AM' -> (10:00, 11:00); '7 PM' -> (19:00, None)."""
    m = _TIME_RANGE.search(text or "")
    if not m:
        return None, None

    def one(s):
        try:
            return dtparse.parse(s.replace(".", "")).time()
        except (ValueError, OverflowError):
            return None
    return one(m.group(1)), (one(m.group(2)) if m.group(2) else None)


def _nth_weekday(year: int, month: int, wd: int, n: int) -> Optional[date]:
    if n > 0:
        d = date(year, month, 1)
        d += timedelta((wd - d.weekday()) % 7 + 7 * (n - 1))
        return d if d.month == month else None
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(1)
    return d - timedelta((d.weekday() - wd) % 7)


def expand_recurrence(recur: str, first: date, last: Optional[date],
                      lo: date, hi: date) -> Optional[list[date]]:
    """Dates of a Simpleview recurrence text inside [lo, hi] and the series span.
    Returns None when the text isn't a pattern we understand."""
    text = (recur or "").lower()
    stop = min(hi, last) if last else hi
    lo = max(lo, first)
    if stop < lo:
        return []
    if re.search(r"\bdaily\b", text):
        return [lo + timedelta(i) for i in range((stop - lo).days + 1)]
    m = re.search(r"(weekly|every (\d+) weeks?) on ([a-z ,]+)", text)
    if m:
        every = int(m.group(2) or 1)
        days = {_WEEKDAYS[w] for w in re.findall(r"[a-z]+", m.group(3)) if w in _WEEKDAYS}
        if not days:
            return None
        anchor = first - timedelta(first.weekday())          # Monday of week one
        out, d = [], lo
        while d <= stop:
            if d.weekday() in days and ((d - anchor).days // 7) % every == 0:
                out.append(d)
            d += timedelta(1)
        return out
    m = re.search(r"monthly on the (1st|2nd|3rd|4th|5th|first|second|third|fourth|fifth|last) "
                  r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", text)
    if m:
        n, wd = _ORDINALS[m.group(1)], _WEEKDAYS[m.group(2)]
        out, y, mo = [], lo.year, lo.month
        while date(y, mo, 1) <= stop:
            d = _nth_weekday(y, mo, wd, n)
            if d and lo <= d <= stop:
                out.append(d)
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        return out
    m = re.search(r"monthly on (?:the |day )?(\d{1,2})(?:st|nd|rd|th)?\b", text)
    if m:
        dom, out, y, mo = int(m.group(1)), [], lo.year, lo.month
        while date(y, mo, 1) <= stop:
            try:
                d = date(y, mo, dom)
                if lo <= d <= stop:
                    out.append(d)
            except ValueError:
                pass
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        return out
    return None


def _detail(url: str) -> dict:
    """The event page's embedded `var data` plus `var time`; {} if unreadable."""
    html = fetch.get_text(url) if url else None
    if not html:
        return {}
    out: dict = {}
    i = html.find("var data = ")
    if i >= 0:
        try:
            out, _ = json.JSONDecoder().raw_decode(html[i + len("var data = "):])
        except ValueError:
            out = {}
    m = re.search(r'var time = "([^"]*)"', html)
    out["time"] = m.group(1) if m else ""
    return out


def events_from_doc(doc: dict, base: str, seen_dates: set[date], detail: dict,
                    today: Optional[date] = None) -> list[Event]:
    """Turn one list doc (plus its event page) into dated occurrences."""
    title = clean_text(doc.get("title") or "")
    start = parse_dt(doc.get("startDate"))
    if not title or not start:
        return []
    end = parse_dt(doc.get("endDate"))
    first = start.date()
    # endDate is 04:59:59Z on the day after the last day = 23:59:59 Central
    last = end.date() if end else None

    media = doc.get("media_raw") or []
    img = media[0].get("mediaurl", "") if media and isinstance(media[0], dict) else ""
    url = doc.get("url") or ""
    if url.startswith("/"):
        url = base.rstrip("/") + url
    recur = clean_text(detail.get("recurrence") or doc.get("recurrence") or "")
    loc = clean_text(doc.get("location") or "")
    t0, t1 = parse_time_range(detail.get("time") or "")
    desc = detail.get("description") or ""
    if recur:
        desc = f"{desc}\n\n{recur}." if desc else f"{recur}."
    price = clean_text(detail.get("admission") or "")

    today = today or datetime.now().date()

    def make(day: date, last_day: Optional[date] = None) -> Event:
        if t0:
            s = datetime.combine(day, t0)
            e = datetime.combine(last_day or day, t1) if t1 else None
            if e and e <= s:
                e = None
            all_day = False
        else:
            s = datetime.combine(day, time())
            e = datetime.combine(last_day, time(23, 59, 59)) if last_day and last_day > day else None
            all_day = True
        return Event(source_id="", title=title, start=s, end=e, venue=loc, address=loc,
                     url=url, description=desc, price=price, image=clean_text(img),
                     category="community", all_day=all_day)

    multi_day = last is not None and last > first
    if not recur and not multi_day:
        return [make(first)] if first >= today - timedelta(days=1) else []

    if re.search(r"\bdaily\b", recur, re.I):
        # an exhibit / festival run: one spanning event, unless it is a short
        # timed run (show at 7 pm nightly) or open-ended (an ongoing special)
        if not last:
            return []
        if t0 and (last - first).days < DAILY_EXPAND_MAX:
            days = expand_recurrence(recur, first, last, today, last) or []
            return [make(d) for d in days]
        return [make(first, last)] if last >= today else []

    days = None
    if recur:
        days = expand_recurrence(recur, first, last, today, today + timedelta(days=SERIES_DAYS))
    if days is None:
        if multi_day and not recur and not seen_dates:
            # a plain multi-day event (festival weekend): one spanning event
            return [make(first, last)] if last >= today else []
        # custom date lists (recurType 99) and unparsed text: only the
        # occurrences the site itself listed
        days = sorted(d for d in seen_dates if d >= today)
    return [make(d) for d in days]


class SimpleviewAdapter:
    name = "simpleview"

    def scrape(self, source: Source) -> AdapterResult:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            return AdapterResult(self.name, ok=False, detail="playwright unavailable")

        events_url = source.events_url or (source.url.rstrip("/") + "/events/")
        from urllib.parse import urlparse
        base = f"{urlparse(events_url).scheme}://{urlparse(events_url).netloc}"

        captured: list[dict] = []
        UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

        try:
            with fetch.playwright_session() as p:
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

        # one doc per event id; a doc can be listed under several dates
        docs_by_id: dict[str, dict] = {}
        dates_by_id: dict[str, set[date]] = {}
        for payload in captured:
            docs = payload.get("docs", {})
            rows = docs.get("docs", []) if isinstance(docs, dict) else []
            for doc in rows:
                key = str(doc.get("_id") or doc.get("recid") or doc.get("url") or "")
                docs_by_id.setdefault(key, doc)
                listed = parse_dt(doc.get("date"))
                if listed:
                    dates_by_id.setdefault(key, set()).add(listed.date())

        events: list[Event] = []
        details = 0
        for key, doc in docs_by_id.items():
            url = doc.get("url") or ""
            detail = _detail(base.rstrip("/") + url if url.startswith("/") else url)
            details += bool(detail)
            events.extend(events_from_doc(doc, base, dates_by_id.get(key, set()), detail))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events ({len(captured)} pages, "
                                    f"{details}/{len(docs_by_id)} event pages)")

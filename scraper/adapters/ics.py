"""Generic iCal / .ics feed adapter.

Discovers a calendar feed from the events page via:
  * <link rel="alternate" type="text/calendar">
  * anchors / buttons whose href ends in .ics or contains ?ical= / outlook-ical
  * the WordPress Events Calendar convention  <events_url>?ical=1
then parses it with the icalendar library.
"""
from __future__ import annotations

from datetime import datetime, date, time
from urllib.parse import urljoin

from icalendar import Calendar
from selectolax.parser import HTMLParser

from models import Event, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch


def _discover(html: str, base: str) -> list[str]:
    tree = HTMLParser(html)
    found: list[str] = []
    for link in tree.css('link[rel="alternate"][type="text/calendar"]'):
        href = link.attributes.get("href")
        if href:
            found.append(urljoin(base, href))
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").lower()
        if href.endswith(".ics") or "ical=1" in href or "outlook-ical" in href or "format=ical" in href:
            found.append(urljoin(base, a.attributes["href"]))
    # common conventions
    found.append(base.rstrip("/") + "/?ical=1")
    # de-dup, keep order
    seen, out = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:5]


def _dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return None


class IcsAdapter:
    name = "ics"

    def _parse(self, text: str, feed_url: str) -> list[Event]:
        try:
            cal = Calendar.from_ical(text)
        except (ValueError, Exception):  # noqa: BLE001
            return []
        out: list[Event] = []
        now = datetime.now()
        for comp in cal.walk("VEVENT"):
            start = _dt(comp.get("dtstart").dt) if comp.get("dtstart") else None
            if not start or start < now.replace(hour=0, minute=0):
                continue
            end = _dt(comp.get("dtend").dt) if comp.get("dtend") else None
            loc = clean_text(str(comp.get("location") or ""))
            out.append(Event(
                source_id="",
                title=clean_text(str(comp.get("summary") or "")),
                start=start,
                end=end,
                venue=loc.split(",")[0] if loc else "",
                address=loc,
                url=clean_text(str(comp.get("url") or feed_url)),
                description=clean_text(str(comp.get("description") or "")),
                all_day=isinstance(comp.get("dtstart").dt, date) and not isinstance(comp.get("dtstart").dt, datetime),
            ))
        return out

    def scrape(self, source: Source) -> AdapterResult:
        html = fetch.get_text(source.target)
        if not html:
            return AdapterResult(self.name, ok=False, detail="fetch failed")
        for feed in _discover(html, source.target):
            text = fetch.get_text(feed)
            if not text or "BEGIN:VCALENDAR" not in text:
                continue
            events = tag_from_source(self._parse(text, feed), source)
            if events:
                return AdapterResult(self.name, events=events, ok=True,
                                     detail=f"{len(events)} events from {feed}")
        return AdapterResult(self.name, ok=False, detail="no usable ics feed")

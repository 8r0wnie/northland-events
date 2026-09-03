"""CivicPlus (CivicEngage) municipal-site adapter.

CivicPlus government sites expose a combined calendar RSS feed at

    /RSSFeed.aspx?ModID=58&CID=All-calendar.xml

Each <item> carries a namespaced block with the parsed date/time/location:

    <calendarEvent:EventDates> September 3, 2026 </calendarEvent:EventDates>
    <calendarEvent:EventTimes>09:30 AM - 11:30 AM</calendarEvent:EventTimes>
    <calendarEvent:Location>1316 N 14th St<br>Boardroom 201Superior, WI 54880</calendarEvent:Location>

Detection: the source is CivicPlus if its HTML mentions "civicplus"/"civicengage"
or exposes a Calendar.aspx page; the adapter also just tries the feed.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

FEED_PATHS = [
    "/RSSFeed.aspx?ModID=58&CID=All-calendar.xml",
    "/RSSFeed.aspx?ModID=65&CID=All-calendar.xml",
]

_NS = {
    "dates": re.compile(r"<calendarEvent:EventDates>(.*?)</calendarEvent:EventDates>", re.S | re.I),
    "times": re.compile(r"<calendarEvent:EventTimes>(.*?)</calendarEvent:EventTimes>", re.S | re.I),
    "loc": re.compile(r"<calendarEvent:Location>(.*?)</calendarEvent:Location>", re.S | re.I),
}
_ITEM = re.compile(r"<item>(.*?)</item>", re.S | re.I)
_TAG = re.compile(r"<(title|link)>(.*?)</\1>", re.S | re.I)
_CITY_STATE = re.compile(r"([A-Za-z .'-]+),\s*([A-Z]{2})\s*\d{5}?")
_TIME_RANGE = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)", re.I)


def _cdata(s: str) -> str:
    s = s.strip()
    m = re.match(r"<!\[CDATA\[(.*?)\]\]>", s, re.S)
    return (m.group(1) if m else s).strip()


def _location(raw: str) -> tuple[str, str, str]:
    """'1316 N 14th St&lt;br&gt;Boardroom 201Superior, WI 54880' -> venue, address, city.

    The RSS packs venue / room / 'City, ST ZIP' onto <br>-separated lines with no
    comma between the last address line and the city.
    """
    if not raw:
        return "", "", ""
    decoded = clean_text(raw)  # unescapes &lt;br&gt; -> <br>
    lines = [clean_text(x) for x in re.split(r"<br\s*/?>", decoded, flags=re.I)]
    lines = [x for x in lines if x]
    if not lines:
        return "", "", ""

    city = ""
    m = re.search(r"([A-Za-z][A-Za-z .'-]*?),?\s*([A-Z]{2})\s*\d{5}(?:-\d{4})?\s*$", lines[-1])
    if m:
        # city is the trailing word(s) of the last line before ", ST ZIP";
        # an address number/street may be glued to its front — trim leading digits.
        chunk = re.sub(r"^\d+\s+", "", m.group(1)).strip()
        # take the last 1-3 capitalized tokens as the city name
        toks = chunk.split()
        city = " ".join(toks[-3:]) if len(toks) > 3 else chunk
        # common case: "<street words><City>" with City a single capitalized run
        cm = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)$", chunk)
        if cm:
            city = cm.group(1)

    if city.islower():
        city = city.title()
    venue = "" if re.match(r"\d", lines[0]) else lines[0]
    full = ", ".join(lines)
    return venue, full, city


def _parse_when(dates: str, times: str) -> tuple[datetime | None, datetime | None, bool]:
    dates = clean_text(dates)
    times = clean_text(times)
    if not dates:
        return None, None, False
    # date range "September 3, 2026 - September 5, 2026"
    parts = re.split(r"\s+-\s+|\s+to\s+", dates)
    start_date = parse_dt(parts[0])
    end_date = parse_dt(parts[1]) if len(parts) > 1 else None
    if not start_date:
        return None, None, False

    all_day = (not times) or "all day" in times.lower()
    tmatch = _TIME_RANGE.findall(times)
    start, end = start_date, end_date
    if tmatch:
        t0 = parse_dt(tmatch[0])
        if t0:
            start = start_date.replace(hour=t0.hour, minute=t0.minute)
        if len(tmatch) > 1:
            t1 = parse_dt(tmatch[1])
            base = end_date or start_date
            if t1:
                end = base.replace(hour=t1.hour, minute=t1.minute)
    return start, end, all_day


class CivicPlusAdapter:
    name = "civicplus"

    def _feed_url(self, source: Source) -> str | None:
        root = f"{urlparse(source.url).scheme}://{urlparse(source.url).netloc}"
        for path in FEED_PATHS:
            text = fetch.get_text(root + path)
            if text and "<rss" in text.lower() and "calendarEvent" in text:
                return root + path
        return None

    def scrape(self, source: Source) -> AdapterResult:
        feed = self._feed_url(source)
        if not feed:
            return AdapterResult(self.name, ok=False, detail="no CivicPlus calendar RSS")
        xml = fetch.get_text(feed)
        if not xml:
            return AdapterResult(self.name, ok=False, detail="feed fetch failed")

        events: list[Event] = []
        for block in _ITEM.findall(xml):
            fields = {k.lower(): _cdata(v) for k, v in _TAG.findall(block)}
            title = clean_text(HTMLParser(fields.get("title", "")).text())
            dates = _NS["dates"].search(block)
            times = _NS["times"].search(block)
            loc = _NS["loc"].search(block)
            start, end, all_day = _parse_when(
                dates.group(1) if dates else "", times.group(1) if times else "")
            if not start or not title:
                continue
            venue, address, city = _location(loc.group(1) if loc else "")
            events.append(Event(
                source_id="",
                title=title,
                start=start,
                end=end,
                all_day=all_day,
                venue=venue,
                address=address,
                city=city,
                url=clean_text(fields.get("link", "")),
                category="community",  # gov calendars are civic meetings/notices
            ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events")

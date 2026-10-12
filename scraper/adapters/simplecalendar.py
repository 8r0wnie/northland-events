"""WordPress "Simple Calendar" (google-calendar-events plugin).

The plugin renders a Google Calendar server-side into the page, so the
events are in the HTML even though the calendar id stays hidden:

    <li class="simcal-event ...">
      <span class="simcal-event-title">Trivia Mafia</span>
      <span class="simcal-event-start" data-event-start="1791849600" ...>
      <span class="simcal-event-end" data-event-end="1791856800" ...>
      <span class="simcal-event-address">...</span>
      <div class="simcal-event-description">...</div>

`data-event-start`/`-end` are Unix seconds (UTC), converted to Central wall
time. "Canceled:" / "Cancelled" entries are skipped. Only what the page
renders is read (usually the current list or month view). Before
2026-10-11 the notes said this plugin "renders nothing": it renders fine,
there just was no adapter. Bent Paddle, Duluth Cider and the Hayward Chamber
use it. One fetch, so it's in the generic chain.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser

from models import Event, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

LOCAL = ZoneInfo("America/Chicago")
_CANCELED = re.compile(r"^\W*cancel+ed\b", re.I)


def _ts(node, attr: str) -> datetime | None:
    if node is None:
        return None
    raw = node.attributes.get(attr) or ""
    if not raw.isdigit():
        return None
    return datetime.fromtimestamp(int(raw), tz=timezone.utc).astimezone(LOCAL).replace(tzinfo=None)


class SimpleCalendarAdapter:
    name = "simplecalendar"

    def scrape(self, source: Source) -> AdapterResult:
        url = source.target
        html = fetch.get_text(url)
        if not html or "simcal-event" not in html:
            return AdapterResult(self.name, detail="no Simple Calendar on page")
        events: list[Event] = []
        seen = set()
        for li in HTMLParser(html).css(".simcal-event"):
            t = li.css_first(".simcal-event-title")
            title = clean_text(t.text(separator=" ")) if t else ""
            start = _ts(li.css_first("[data-event-start]"), "data-event-start")
            if not title or not start or _CANCELED.search(title):
                continue
            if (title.lower(), start) in seen:
                continue
            seen.add((title.lower(), start))
            end = _ts(li.css_first("[data-event-end]"), "data-event-end")
            a = li.css_first(".simcal-event-title a[href], a[href]")
            addr = li.css_first(".simcal-event-address")
            desc = li.css_first(".simcal-event-description")
            # all-day events are rendered with a date-only format and no time span
            fmt = (li.css_first("[data-event-start]").attributes.get("data-event-format") or "")
            all_day = start.hour == 0 and start.minute == 0 and not any(c in fmt for c in "gGhHi")
            events.append(Event(
                source_id="", title=title, start=start,
                end=end if end and end > start else None, all_day=all_day,
                address=clean_text(addr.text(separator=" ")) if addr else "",
                url=urljoin(url, a.attributes.get("href")) if a else url,
                description=clean_text(desc.text(separator=" ")) if desc else "",
                category="other",
            ))
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events")

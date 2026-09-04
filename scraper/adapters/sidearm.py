"""Sidearm Sports adapter — college athletics schedules.

Sidearm powers most college athletics sites and exposes a public iCal feed at
`<host>/calendar.ashx/calendar.ics` (no key). SUMMARY lines are prefixed with a
bracket tag: [H] home, [A] away, [N] neutral site, plus [W]/[L]/[T] once a
result is in. We keep home and neutral games (away games happen elsewhere) and
strip the tag.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from icalendar import Calendar

try:
    from zoneinfo import ZoneInfo
    _CENTRAL = ZoneInfo("America/Chicago")
except Exception:  # noqa: BLE001
    _CENTRAL = None

from models import Event, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

FEED = "/calendar.ashx/calendar.ics"
HORIZON_DAYS = 150
_TAG = re.compile(r"^\s*\[([HANWLT])\]\s*", re.I)
_SCHOOL = re.compile(
    r"^(University of Minnesota Duluth|University of Wisconsin-Superior|"
    r"The College of St\.? Scholastica|College of St\.? Scholastica)\s+", re.I)
_SHORT = {"university of minnesota duluth": "UMD",
          "university of wisconsin-superior": "UW-Superior",
          "the college of st. scholastica": "St. Scholastica"}


def _dt(v):
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            return (v.astimezone(_CENTRAL) if _CENTRAL else v).replace(tzinfo=None)
        return v
    if isinstance(v, date):
        return datetime.combine(v, time(0, 0))
    return None


class SidearmAdapter:
    name = "sidearm"

    def scrape(self, source: Source) -> AdapterResult:
        from urllib.parse import urlparse
        host = f"{urlparse(source.url).scheme}://{urlparse(source.url).netloc}"
        text = fetch.get_text(host + FEED)
        if not text or "BEGIN:VCALENDAR" not in text:
            return AdapterResult(self.name, ok=False, detail="no Sidearm iCal feed")

        try:
            cal = Calendar.from_ical(text)
        except Exception:  # noqa: BLE001
            return AdapterResult(self.name, ok=False, detail="unparseable iCal")

        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        horizon = now + timedelta(days=HORIZON_DAYS)
        events: list[Event] = []
        home_cities = {source.area.lower()} | {source.area.lower().split()[0]}
        for comp in cal.walk("VEVENT"):
            summary = clean_text(str(comp.get("summary") or ""))
            title = _TAG.sub("", summary).strip()
            if not title:
                continue
            m = _SCHOOL.match(title)
            if m:
                title = f"{_SHORT.get(m.group(1).lower(), m.group(1))} {title[m.end():]}".strip()
            loc = clean_text(str(comp.get("location") or ""))
            low = f" {title.lower()} "
            # Home games read "X vs Y"; away/tournament games read "X at Y" or
            # carry a non-local location. Keep only what actually happens here.
            is_away = " at " in low or " @ " in low
            local = any(city and city in loc.lower() for city in home_cities)
            if is_away and not local:
                continue
            if not is_away and not local and loc:
                continue
            start = _dt(comp.get("dtstart").dt) if comp.get("dtstart") else None
            if not start or not (now - timedelta(days=1) <= start <= horizon):
                continue
            end = _dt(comp.get("dtend").dt) if comp.get("dtend") else None
            loc = clean_text(str(comp.get("location") or ""))
            events.append(Event(
                source_id="",
                title=title,
                start=start,
                end=end,
                all_day=isinstance(comp.get("dtstart").dt, date)
                        and not isinstance(comp.get("dtstart").dt, datetime),
                venue=loc.split(",")[0] if loc else "",
                address=loc,
                url=clean_text(str(comp.get("url") or "")) or source.url,
                description=clean_text(str(comp.get("description") or "")),
                category="sports-rec",
            ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} home/neutral events")

"""HeyGov adapter.

HeyGov-powered municipal sites embed a calendar backed by a public API:

    GET https://api.heygov.com/<domain>/events?month=YYYY-MM&expand=parent&source=calendar-embed

returning a JSON list of events with name / description / location /
starts_at_local / ends_at_local (the *_local fields are local wall time despite
the trailing Z).
"""
from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

MONTHS_AHEAD = 6


def _domain(source: Source) -> str:
    return urlparse(source.url).netloc.replace("www.", "")


def _months(n: int):
    y, m = date.today().year, date.today().month
    for _ in range(n):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _local(ts: str):
    dt = parse_dt((ts or "").replace("Z", ""))
    return dt.replace(tzinfo=None) if dt else None


class HeyGovAdapter:
    name = "heygov"

    def scrape(self, source: Source) -> AdapterResult:
        domain = _domain(source)
        api = f"https://api.heygov.com/{domain}/events"
        seen: set[str] = set()
        events: list[Event] = []
        ok_any = False

        for i, month in enumerate(_months(MONTHS_AHEAD)):
            r = fetch.get(api, params={"month": month, "expand": "parent", "source": "calendar-embed"})
            if r is None:
                # a hard failure on the first month means this isn't a HeyGov site
                if i == 0:
                    return AdapterResult(self.name, ok=False, detail="HeyGov API rejected domain")
                continue
            try:
                rows = r.json()
            except ValueError:
                return AdapterResult(self.name, ok=False, detail="non-JSON response")
            ok_any = True
            if isinstance(rows, dict):
                rows = rows.get("events") or rows.get("data") or []
            for node in rows:
                pid = node.get("pid") or ""
                start = _local(node.get("starts_at_local") or node.get("starts_at"))
                if not start or pid in seen:
                    continue
                seen.add(pid)
                loc = node.get("location") or ""
                venues = node.get("venues") or []
                if not loc and venues and isinstance(venues[0], dict):
                    loc = venues[0].get("name", "")
                events.append(Event(
                    source_id="",
                    title=clean_text(node.get("name") or ""),
                    start=start,
                    end=_local(node.get("ends_at_local") or node.get("ends_at")),
                    venue=clean_text(loc),
                    address=clean_text(loc),
                    url=f"{source.url.rstrip('/')}/events",
                    description=clean_text(HTMLParser(node.get("description") or "").text()),
                    category="community",
                ))

        if not ok_any:
            return AdapterResult(self.name, ok=False, detail="HeyGov API unreachable")
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events), detail=f"{len(events)} events")

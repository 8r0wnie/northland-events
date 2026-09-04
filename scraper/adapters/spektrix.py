"""Spektrix adapter — arts/ticketing venues (e.g. Big Top Chautauqua).

Spektrix exposes a public read API at
`system.spektrix.com/<client>/api/v3/events` (+ `/events/<id>/instances` for
each performance date). The `<client>` slug is discovered from a
`spektrix.com/<client>` or `spektrix-link.com/clients/<client>` reference on the
venue's ticketing page.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text, infer_category
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

HORIZON_DAYS = 300
MAX_INSTANCES = 400
# most reliable: the explicit "clients/<slug>" form; the bare form is a fallback
_CLIENT_STRONG = re.compile(r"spektrix(?:-link)?\.com/clients/([a-z0-9_-]+)", re.I)
_CLIENT_WEAK = re.compile(r"(?:system|tickets|www)\.spektrix\.com/([a-z0-9_-]+)/", re.I)
_NOT_CLIENT = {"stable", "js", "api", "clients", "integrate", "system", "tickets", "v3", "assets"}


def _client_slug(source: Source) -> str | None:
    # allow events_url to name the client directly, e.g.
    # https://system.spektrix.com/<client>  or  spektrix:<client>
    if source.events_url:
        m = re.search(r"spektrix\.com/([a-z0-9_-]+)|^spektrix:([a-z0-9_-]+)$",
                      source.events_url, re.I)
        if m and (m.group(1) or m.group(2)).lower() not in _NOT_CLIENT:
            return m.group(1) or m.group(2)

    for page in (source.events_url, source.url):
        if not page or page.startswith("spektrix:"):
            continue
        html = fetch.get_text(page)
        if not html:
            continue
        m = _CLIENT_STRONG.search(html)
        if m:
            return m.group(1)
        for m in _CLIENT_WEAK.finditer(html):
            if m.group(1).lower() not in _NOT_CLIENT:
                return m.group(1)
    return None


class SpektrixAdapter:
    name = "spektrix"

    def scrape(self, source: Source) -> AdapterResult:
        slug = _client_slug(source)
        if not slug:
            return AdapterResult(self.name, ok=False, detail="no Spektrix client slug found")
        api = f"https://system.spektrix.com/{slug}/api/v3"
        start_from = date.today().isoformat()
        r = fetch.get(f"{api}/events", params={"startFrom": start_from})
        if r is None:
            return AdapterResult(self.name, ok=False, detail="events API request failed")
        try:
            listing = r.json()
        except ValueError:
            return AdapterResult(self.name, ok=False, detail="non-JSON response")
        if not isinstance(listing, list):
            return AdapterResult(self.name, ok=False, detail="unexpected payload")

        now = datetime.now()
        horizon = now + timedelta(days=HORIZON_DAYS)
        events: list[Event] = []
        n_instances = 0

        for ev in listing:
            if str(ev.get("attribute_SLExcludeFromViewEventsPage", "")).lower() == "true":
                continue
            name = clean_text(ev.get("name") or "")
            if not name:
                continue
            desc = clean_text(HTMLParser(ev.get("htmlDescription") or "").text()
                              or ev.get("description") or "")
            img = clean_text(ev.get("imageUrl") or ev.get("thumbnailUrl") or "")
            dur = ev.get("duration") or 0
            show_type = clean_text(ev.get("attribute_ShowType") or "")
            cat = infer_category(f"{name} {show_type}", desc, "music")

            starts: list[datetime] = []
            first = parse_dt(ev.get("firstInstanceDateTime"))
            last = parse_dt(ev.get("lastInstanceDateTime"))
            if first and last and first.date() != last.date() and n_instances < MAX_INSTANCES:
                inst = fetch.get(f"{api}/events/{ev.get('id')}/instances")
                if inst is not None:
                    try:
                        for row in inst.json():
                            dt = parse_dt(row.get("start"))
                            if dt:
                                starts.append(dt)
                    except ValueError:
                        pass
            if not starts and first:
                starts = [first]

            for dt in starts:
                if not (now - timedelta(days=1) <= dt <= horizon):
                    continue
                n_instances += 1
                events.append(Event(
                    source_id="",
                    title=name,
                    start=dt,
                    end=dt + timedelta(minutes=dur) if dur else None,
                    url=source.events_url or source.url,
                    description=desc,
                    image=img,
                    category=cat,
                ))

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} performances (client {slug})")

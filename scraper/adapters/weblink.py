"""WebLink Connect ("Atlas") adapter.

Chambers on WebLink serve their public site as an Angular SPA at
`<tenant>.weblinkconnect.com/atlas/`, backed by a public JSON API:

    GET https://api-internal.weblinkconnect.com/api/Events
        ?PageSize=0&OrganizationEvent=true&CommunityEvent=true
        &MembersOnlyEvent=true&InternalEvent=false&EventClosed=false
        &SearchDateBegin=<iso>&SearchDateEnd=<iso>
    header  x-tenant: <tenant>          (case-insensitive, no auth token needed)

The tenant slug is the subdomain, discovered from the source's homepage.
Event detail pages: https://<tenant>.weblinkconnect.com/atlas/events/<id>/details
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

API = "https://api-internal.weblinkconnect.com/api/Events"
MONTHS_AHEAD = 6
_TENANT = re.compile(r"([a-z0-9-]+)\.weblinkconnect\.com", re.I)


def _tenant(source: Source) -> str | None:
    for text in (source.events_url, source.url):
        if text:
            m = _TENANT.search(text)
            if m:
                return m.group(1)
    home = fetch.get_text(source.url)
    if home:
        m = _TENANT.search(home)
        if m:
            return m.group(1)
    return None


def _combine(date_str: str, time_str: str) -> datetime | None:
    base = parse_dt(date_str)
    if not base:
        return None
    base = base.replace(tzinfo=None)
    t = parse_dt(time_str) if time_str else None
    if t:
        return base.replace(hour=t.hour, minute=t.minute)
    return base


def _to_event(node: dict, tenant: str) -> Event | None:
    start = _combine(node.get("StartDate", ""), node.get("StartTime", ""))
    if not start:
        return None
    end = _combine(node.get("EndDate", "") or node.get("StartDate", ""), node.get("EndTime", ""))
    venue = clean_text(node.get("Venue") or node.get("Address1") or "")
    addr = ", ".join(p for p in [
        clean_text(node.get("Address1", "")), clean_text(node.get("Address2", "")),
        clean_text(node.get("City", "")), clean_text(node.get("State", "")),
    ] if p)
    eid = node.get("EventId")
    return Event(
        source_id="",
        title=clean_text(node.get("EventName") or ""),
        start=start,
        end=end,
        all_day=bool(node.get("IsAllDay")),
        venue=venue,
        address=addr,
        city=clean_text(node.get("City") or ""),
        state=clean_text(node.get("State") or ""),
        url=f"https://{tenant}.weblinkconnect.com/atlas/events/{eid}/details" if eid else "",
        description=clean_text(_strip(node.get("Descr") or node.get("ShortDescr") or "")),
        image=clean_text(node.get("ImageUrl") or ""),
    )


def _strip(s: str) -> str:
    from selectolax.parser import HTMLParser
    return HTMLParser(s).text() if s and "<" in s else s


class WebLinkAdapter:
    name = "weblink"

    def scrape(self, source: Source) -> AdapterResult:
        tenant = _tenant(source)
        if not tenant:
            return AdapterResult(self.name, ok=False, detail="no weblinkconnect tenant found")

        now = datetime.now(timezone.utc)
        params = {
            "PageSize": 0,
            "OrganizationEvent": "true", "CommunityEvent": "true",
            "MembersOnlyEvent": "true", "InternalEvent": "false", "EventClosed": "false",
            "SearchDateBegin": now.strftime("%Y-%m-%dT00:00:00.000Z"),
            "SearchDateEnd": (now + timedelta(days=31 * MONTHS_AHEAD)).strftime("%Y-%m-%dT00:00:00.000Z"),
        }
        r = fetch.get(API, params=params, headers={"x-tenant": tenant})
        if r is None:
            return AdapterResult(self.name, ok=False, detail="API request failed")
        try:
            payload = r.json()
        except ValueError:
            return AdapterResult(self.name, ok=False, detail="non-JSON response")

        events = [e for e in (_to_event(n, tenant) for n in payload.get("Result", [])) if e]
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events (tenant {tenant})")

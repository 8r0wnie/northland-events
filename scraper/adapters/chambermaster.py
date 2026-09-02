"""ChamberMaster / GrowthZone ("MNI") Member Information Center adapter.

These sites (a big share of the region's chambers) serve a server-rendered
event calendar at  <mic>/events/calendar/<YYYY-MM-01>  and per-event detail
pages at  <mic>/events/details/<slug>-<id>  that carry schema.org microdata
(itemprop=startDate / endDate / name / description / location).

Strategy: walk the month calendars to enumerate detail URLs, then parse each
detail page's microdata. The MIC often lives on a `business.<domain>` or
`<name>.chambermaster.com` host, so we probe a few candidates.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

MONTHS_AHEAD = 5
MAX_DETAILS = 160

_CITY_STATE = re.compile(r"^(.*?),\s*([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?$")


def _first_month() -> date:
    t = date.today()
    return date(t.year, t.month, 1)


def _iter_months(start: date, n: int):
    y, m = start.year, start.month
    for _ in range(n):
        yield date(y, m, 1)
        m += 1
        if m > 12:
            m, y = 1, y + 1


_MIC_HOST = re.compile(
    r"https?://([a-z0-9-]+\.(?:chambermaster\.com|growthzoneapp\.com|growthzonesites\.com))",
    re.I,
)


def _mic_candidates(source: Source) -> list[str]:
    cands: list[str] = []
    if source.events_url:
        cands.append(source.events_url.split("/events")[0])
    root = source.url.rstrip("/")
    bare = urlparse(root).netloc
    bare = bare[4:] if bare.startswith("www.") else bare
    cands += [root, f"https://business.{bare}", f"https://members.{bare}"]

    # The hosted MIC subdomain is usually linked from the homepage.
    home = fetch.get_text(root)
    if home:
        for host in dict.fromkeys(_MIC_HOST.findall(home)):
            cands.append(f"https://{host.lower()}")

    return list(dict.fromkeys(cands))


def _location_lines(tree: HTMLParser) -> list[str]:
    """The location block is <div itemprop=name> with <br>-separated lines:
    venue / street / 'City, ST ZIP'."""
    el = tree.css_first('[itemprop="location"] [itemprop="name"]') or tree.css_first('[itemprop="location"]')
    if not el:
        return []
    text = el.text(separator="\n")
    lines = [clean_text(x) for x in text.split("\n")]
    return [x for x in lines if x and x.lower() != "location:"]


def _parse_location(lines: list[str]) -> tuple[str, str, str]:
    if not lines:
        return "", "", ""
    venue = lines[0]
    address = ", ".join(lines[1:]) if len(lines) > 1 else ""
    city = ""
    for ln in reversed(lines):
        m = _CITY_STATE.match(ln)
        if m:
            city = m.group(1).strip()
            break
    return venue, address, city


def _parse_detail(html: str, url: str) -> Event | None:
    tree = HTMLParser(html)

    def ip(name: str) -> str:
        el = tree.css_first(f'[itemprop="{name}"]')
        if not el:
            return ""
        return clean_text(el.attributes.get("content") or el.text(strip=True))

    start = parse_dt(ip("startDate"))
    if not start:
        return None
    og_title = tree.css_first("meta[property='og:title']")
    title = (clean_text(og_title.attributes.get("content", "")) if og_title else "") or ip("name")
    venue, address, city = _parse_location(_location_lines(tree))
    desc = ip("description")
    if not desc:
        og = tree.css_first("meta[property='og:description']")
        desc = clean_text(og.attributes.get("content", "")) if og else ""
    img = tree.css_first("meta[property='og:image']")
    return Event(
        source_id="",
        title=title,
        start=start,
        end=parse_dt(ip("endDate")),
        venue=venue,
        address=address,
        city=city,
        url=url,
        description=desc,
        image=clean_text(img.attributes.get("content", "")) if img else "",
    )


class ChamberMasterAdapter:
    name = "chambermaster"

    def _detail_links(self, html: str, base: str) -> set[str]:
        tree = HTMLParser(html)
        out = set()
        for a in tree.css('a[href*="/events/details/"]'):
            out.add(urljoin(base, a.attributes["href"].split("#")[0]))
        return out

    def scrape(self, source: Source) -> AdapterResult:
        mic = None
        for cand in _mic_candidates(source):
            probe = fetch.get_text(f"{cand}/events/calendar/{_first_month().isoformat()}")
            if probe and "/events/details/" in probe:
                mic = cand
                first_html = probe
                break
        if not mic:
            return AdapterResult(self.name, ok=False, detail="no ChamberMaster MIC found")

        detail_urls: set[str] = set(self._detail_links(first_html, mic))
        for month in list(_iter_months(_first_month(), MONTHS_AHEAD))[1:]:
            html = fetch.get_text(f"{mic}/events/calendar/{month.isoformat()}")
            if html:
                detail_urls |= self._detail_links(html, mic)

        events: list[Event] = []
        for url in list(detail_urls)[:MAX_DETAILS]:
            html = fetch.get_text(url)
            if not html:
                continue
            ev = _parse_detail(html, url)
            if ev:
                events.append(ev)

        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events via {mic}")

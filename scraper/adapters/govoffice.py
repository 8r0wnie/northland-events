"""GovOffice (Vision/GovOffice.com, newer responsive '/repository/designs/' build).

The events page at <root>/events renders a month calendar grid (the CM/CY month
params are ignored — it always shows the current month, but each cell's tooltip
carries the absolute date so a few pages usually suffice). Cell markup:

    <td class="calDay calEvent">
      <span class="calDayNum">7</span>
      <a class="eventLink" href="/index.asp?SEC=...&DE=...">
        <span class="eventTitle">Title</span>
        <div class="eventTip"><div class="tipTitle">Title</div>
             Monday, September 7, 2026 at 8:00 AM</div>

A third layout (list view, e.g. Sandstone) renders one block per event:

    <article class="summaryDisplay">
      <h2><a class="item" href="/index.asp?SEC=...&DE=...&Type=B_EV">Title</a></h2>
      <time>Thursday, February 26, 2026 at 6:00 PM</time>

The events page moves between sites (/events, /calendar, or an
index.asp?SEC=<guid> page), so a pinned `events_url` is tried first, then the
usual paths, then any homepage link labelled calendar/events.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from models import Event, parse_dt, clean_text
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

_TIP_DATE = re.compile(
    r"([A-Z][a-z]+day,\s+[A-Z][a-z]+\s+\d{1,2},\s+\d{4}(?:\s+at\s+\d{1,2}:\d{2}\s*[AP]M)?)")

CANDIDATE_PATHS = ["/events", "/calendar", "/community-calendar", "/meeting-calendar"]


class GovOfficeAdapter:
    name = "govoffice"

    def _parse(self, html: str, base: str, seen: set) -> list[Event]:
        tree = HTMLParser(html)
        out: list[Event] = []
        # Two GovOffice cell layouts: an .eventTip tooltip, or an .eventDates sibling.
        for link in tree.css("a.eventLink"):
            title_el = link.css_first(".eventTitle") or link.css_first(".tipTitle")
            title = clean_text(title_el.text()) if title_el else clean_text(link.text())

            date_txt = ""
            for sib in (link.css_first(".eventTip"), link.next):
                if sib is not None:
                    date_txt = sib.text(separator=" ") if hasattr(sib, "text") else str(sib)
                    if _TIP_DATE.search(date_txt):
                        break
            if not _TIP_DATE.search(date_txt):
                parent = link.parent
                date_txt = parent.text(separator=" ") if parent else ""

            m = _TIP_DATE.search(date_txt)
            start = parse_dt(m.group(1).replace(" at ", " ")) if m else None
            if not title or not start:
                continue
            key = (title.lower(), start.date())
            if key in seen:
                continue
            seen.add(key)
            href = link.attributes.get("href", "")
            out.append(Event(
                source_id="",
                title=title,
                start=start,
                all_day=" at " not in date_txt,
                url=urljoin(base, href) if href else base,
                category="community",
            ))
        return out

    def _parse_list(self, html: str, base: str, seen: set) -> list[Event]:
        tree = HTMLParser(html)
        out: list[Event] = []
        for art in tree.css("article.summaryDisplay"):
            a = art.css_first("h2 a, h3 a, a.item")
            t = art.css_first("time")
            if not a or not t:
                continue
            title = clean_text(a.text())
            m = _TIP_DATE.search(clean_text(t.text(separator=" ")))
            start = parse_dt(m.group(1).replace(" at ", " ")) if m else None
            if not title or not start or (title.lower(), start.date()) in seen:
                continue
            seen.add((title.lower(), start.date()))
            href = a.attributes.get("href", "")
            out.append(Event(source_id="", title=title, start=start,
                             all_day=" at " not in m.group(1),
                             url=urljoin(base, href) if href else base, category="community"))
        return out

    def _candidates(self, source: Source, base: str) -> list[str]:
        urls = []
        if source.events_url:
            urls.append(source.events_url)
        urls += [base + p for p in CANDIDATE_PATHS]
        home = fetch.get_text(base)
        if home:
            for a in HTMLParser(home).css("a[href]"):
                label = clean_text(a.text()).lower()
                href = urljoin(base, a.attributes.get("href") or "")
                if urlparse(href).netloc == urlparse(base).netloc and re.search(
                        r"calendar|events", label) and "agenda" not in label:
                    urls.append(href)
        seen, out = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out[:8]

    def scrape(self, source: Source) -> AdapterResult:
        p = urlparse(source.url)
        base = f"{p.scheme}://{p.netloc}"
        seen: set = set()
        events: list[Event] = []
        pages = 0

        for url in self._candidates(source, base):
            html = fetch.get_text(url)
            if not html or ("eventLink" not in html and "summaryDisplay" not in html):
                continue
            pages += 1
            events += self._parse(html, base, seen) + self._parse_list(html, base, seen)

        if not pages:
            return AdapterResult(self.name, ok=False, detail="no GovOffice events page")
        events = tag_from_source(events, source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} events from {pages} page(s)")

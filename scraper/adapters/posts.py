"""Text-inference fallback for sites with no machine-readable calendar.

Many small-town, county, and venue sites never publish a calendar, but they do
announce events: a WordPress news post ("Fall Festival returns October 18"), a
Weebly/Squarespace blog, an "Upcoming Events" page of plain paragraphs, or a
list like "Oct 18 - Harvest Fest, 10am-2pm, City Park". This adapter harvests
those announcements.

Documents it reads (all public, all plain GET):
  1. the source's events page (headless render if it's a near-empty JS shell);
  2. WordPress posts via `/wp-json/wp/v2/posts` when the site is WordPress;
  3. RSS/Atom feeds the page advertises, plus platform conventions
     (WordPress `/feed/`, Wix `/blog-feed.xml`, Squarespace `?format=rss`);
  4. up to MAX_FOLLOW same-site pages linked with event/news/calendar wording.

Extraction is deliberately conservative:
  * a date must be explicit ("Oct. 18", "Saturday, October 18, 2026",
    "July 8-11", "10/18/2026"); a weekday that disagrees with the date
    rejects it;
  * dates right after "posted / updated / deadline / due / by / until" etc.
    are ignored, and agenda/minutes/closure text is skipped;
  * the text must carry an event signal (festival, concert, join us, ...);
  * only dates in [today - 1 day, today + HORIZON_DAYS] survive.

Every event it emits is flagged `inferred=True`, so a lone inferred event goes
to the review queue instead of straight onto the public calendar (verify.py).
Pin with `platform: posts`, or chain after a calendar adapter
(`platform: govoffice,posts`) so news posts back up an empty calendar.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from selectolax.parser import HTMLParser

from models import Event, clean_text, parse_dt
from registry import Source
from adapters.base import AdapterResult, tag_from_source
import fetch

HORIZON_DAYS = 200
MAX_FOLLOW = 6
MAX_EVENTS = 60
MIN_TEXT_BEFORE_RENDER = 400

_MON = (r"(?P<{n}>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
        r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_WD = r"(?P<wd>mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|nesday|rsday|urday|sday)?\.?,?\s+"
_ORD = r"(?:st|nd|rd|th)?"
_SEP = r"\s*(?:-|–|—|to|through|thru|&|and)\s*"

DATE_RE = re.compile(
    rf"(?:{_WD})?{_MON.format(n='mon')}\s+(?P<d1>\d{{1,2}}){_ORD}\b"
    rf"(?:{_SEP}(?:{_MON.format(n='mon2')}\s+)?(?P<d2>\d{{1,2}}){_ORD}\b)?"
    rf"(?:,?\s+(?P<year>20\d\d))?",
    re.I,
)
NUM_DATE_RE = re.compile(r"(?<![\d/])(?P<m>1[0-2]|0?[1-9])/(?P<d>3[01]|[12]\d|0?[1-9])/(?P<y>20\d\d|\d\d)(?![\d/])")
DMY_DATE_RE = re.compile(
    rf"(?:{_WD})?(?P<d1>3[01]|[12]\d|0?[1-9])\s+{_MON.format(n='mon')},?\s+(?P<year>20\d\d)\b", re.I)
# "7 pm", "7:30 p.m.", and the calendar-grid short form "4:30p" / "9a"
TIME_RE = re.compile(r"\b(?P<h>1[0-2]|0?[1-9])(?::(?P<mi>[0-5]\d))?"
                     r"(?:\s*(?P<ap>a\.?\s?m\.?|p\.?\s?m\.?)|(?P<ap2>[ap])(?![a-z]))", re.I)
# sentence ends, but not abbreviations common in venue names ("Bayfield St.")
_SENT_END = re.compile(r"(?<=[a-z0-9)])(?<!\bSt)(?<!\bMt)(?<!\bDr)(?<!\bAve)(?<!\bRd)"
                       r"(?<!\bNo)(?<!\bCo)(?<!\bJr)(?<!\bSr)[.!?](?:\s|$)")

# a date preceded by one of these is a publication/deadline date, not an event
_NEG_BEFORE = re.compile(
    r"(posted|published|updated|modified|deadline|due|expires?|closed|closing|"
    r"applications?|apply|submit\w*|returned|received|approved|adopted|dated|"
    r"effective|by|until|before|no later than|as of|since|registration ends)"
    r"\s*(?:on|:)?\s*$", re.I)
# blocks about these are not public events
_NEG_TEXT = re.compile(
    r"\b(agenda|minutes|meeting packet|closed for|office (?:will be )?closed|"
    r"holiday hours|bid|rfp|request for proposals?|ordinance|resolution|"
    r"public notice|legal notice|job|position|hiring|employment|tax|assessment|"
    r"copyright|all rights reserved|privacy policy)\b", re.I)
_SIGNAL = re.compile(
    r"\b(event|festival|fest|fair|concert|celebrat\w*|parade|market|open house|"
    r"show|tournament|race|5k|10k|run|walk|ride|dance|party|fundraiser|benefit|"
    r"dinner|breakfast|supper|brunch|lunch|picnic|potluck|fish fry|workshop|class|"
    r"program|story ?time|movie|film|music|live|performance|play|theat(?:er|re)|"
    r"exhibit\w*|gallery|art|craft|bazaar|sale|auction|powwow|pow wow|ceremony|"
    r"clean-?up|bingo|trivia|tour|hike|fishing derby|derby|rodeo|tractor|car show|"
    r"carnival|rummage|book club|lecture|talk|presentation|celebration|gathering|"
    r"join us|all are welcome|everyone is welcome|free admission|tickets?)\b", re.I)
_MONTH_WORD = (r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?")
_GENERIC_TITLES = re.compile(
    r"^(?:(upcoming )?(events?|calendar|news|announcements?|home|welcome|"
    r"community events|news (?:&|and) events|what'?s happening|latest news|"
    r"read more|learn more|more info|details|register|registration|tickets|"
    r"begins?|ends?|starts?|time|times|dates?|when|where|location|cost|price|"
    r"admission|contact|info|information|description|summary|amenities|hours)"
    r"|(?:(?:upcoming )?events? (?:in|for|during) )?" + _MONTH_WORD + r"(?: \d{1,2})?,? ?(?:\d{4})?"
    r"|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,? .{0,25}"
    r"|\d{1,2}(?:/\d{1,2})?(?:/\d{2,4})?)$", re.I)
# calendar-grid / widget chrome that carries dates but no event
_GRID_NOISE = re.compile(r"\b(no events|nothing scheduled|there are no|previous month|"
                         r"next month|today\b.*\bmonth\b|view (?:as|by) (?:list|month|week))", re.I)
_FOLLOW_HINT = re.compile(r"event|calendar|news|happening|festival|fair|things-to-do|"
                          r"schedule|whats-on|activities|community|announcement|blog", re.I)
_SKIP_HREF = re.compile(r"\.(pdf|docx?|xlsx?|jpe?g|png|gif|zip)(\?|$)|agenda|minutes|"
                        r"mailto:|tel:|javascript:|facebook\.com|twitter\.com|instagram\.com", re.I)


# ── date parsing ─────────────────────────────────────────────────────────────

MAX_YEARLESS_AHEAD = 120   # "Feb 23rd" read in October is almost always past


def _infer_year(month: int, day: int, ref: date) -> date | None:
    """Year for a date written without one. Announcements without a year are
    near-term, so a result more than MAX_YEARLESS_AHEAD days out is treated
    as a past mention ("The final pour. February 23rd.") and dropped."""
    for y in (ref.year, ref.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            return None
        if d >= ref - timedelta(days=30):
            return d if d <= ref + timedelta(days=MAX_YEARLESS_AHEAD) else None
    return None


def _weekday_ok(wd: str | None, d: date) -> bool:
    if not wd:
        return True
    return d.strftime("%a").lower()[:3] == wd.lower()[:3]


def find_dates(text: str, ref: date) -> list[tuple[date, date | None, int, int]]:
    """Return (start, end, match_start, match_end) for each explicit date in text."""
    out = []
    for m in DATE_RE.finditer(text):
        if _NEG_BEFORE.search(text[max(0, m.start() - 40):m.start()]):
            continue
        mon = _MONTH_NUM[m.group("mon").lower()[:3]]
        d1 = int(m.group("d1"))
        year = m.group("year")
        try:
            start = date(int(year), mon, d1) if year else _infer_year(mon, d1, ref)
        except ValueError:
            continue
        if not start:
            continue
        if not _weekday_ok(m.group("wd"), start):
            # "Saturday, October 18" with no year: maybe next year's date
            if year:
                continue
            try:
                alt = date(start.year + 1, mon, d1)
            except ValueError:
                continue
            if not _weekday_ok(m.group("wd"), alt):
                continue
            start = alt
        end = None
        if m.group("d2"):
            mon2 = _MONTH_NUM[m.group("mon2").lower()[:3]] if m.group("mon2") else mon
            try:
                end = date(start.year + (1 if mon2 < mon else 0), mon2, int(m.group("d2")))
            except ValueError:
                end = None
            if end and not (start < end <= start + timedelta(days=21)):
                end = None
        out.append((start, end, m.start(), m.end()))
    for m in NUM_DATE_RE.finditer(text):
        if _NEG_BEFORE.search(text[max(0, m.start() - 40):m.start()]):
            continue
        y = int(m.group("y"))
        y = y + 2000 if y < 100 else y
        try:
            out.append((date(y, int(m.group("m")), int(m.group("d"))), None, m.start(), m.end()))
        except ValueError:
            pass
    # day-first with a year, as Drupal/CivicPlus HCMS calendars print it:
    # "Mon, 05 Oct 2026". Skip spans the month-first pattern already took.
    taken = [(a, b) for *_, a, b in out]
    for m in DMY_DATE_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        if _NEG_BEFORE.search(text[max(0, m.start() - 40):m.start()]):
            continue
        try:
            d = date(int(m.group("year")), _MONTH_NUM[m.group("mon").lower()[:3]], int(m.group("d1")))
        except ValueError:
            continue
        if _weekday_ok(m.group("wd"), d):
            out.append((d, None, m.start(), m.end()))
    return sorted(out, key=lambda t: t[2])


# "4:30-6:30 p.m.", "10-11 a.m.", "noon-1 p.m.": the start borrows the end's meridiem
_RANGE_RE = re.compile(r"\b(?P<h>1[0-2]|0?[1-9]|noon)(?::(?P<mi>[0-5]\d))?\s*[-–]\s*"
                       r"(?P<eh>1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?P<ap>[ap])\.?\s?m\b", re.I)
_NOON = re.compile(r"\bnoon\b", re.I)


def find_time(text: str):
    """(hour, minute) of the first time in text, or None."""
    m = TIME_RE.search(text)
    r = _RANGE_RE.search(text)
    n = _NOON.search(text)
    starts = [(x.start(), k) for x, k in ((m, "t"), (r, "r"), (n, "n")) if x]
    if not starts:
        return None
    _, kind = min(starts)
    if kind == "n" and not (r and r.start() == n.start()):
        return 12, 0
    if kind in ("r", "n") and r:
        if r.group("h").lower() == "noon":
            return 12, 0
        h = int(r.group("h")) % 12
        end_h = int(r.group("eh")) % 12
        # "10-11 a.m." -> am; "4:30-6:30 p.m." -> pm; "11-1 p.m." -> 11 am
        if r.group("ap").lower() == "p" and h <= end_h:
            h += 12
        return h, int(r.group("mi") or 0)
    h = int(m.group("h")) % 12
    if (m.group("ap") or m.group("ap2")).lower().startswith("p"):
        h += 12
    return h, int(m.group("mi") or 0)


# ── document gathering ───────────────────────────────────────────────────────

def _visible_text_len(html: str) -> int:
    tree = HTMLParser(html)
    for sel in ("script", "style", "noscript"):
        for n in tree.css(sel):
            n.decompose()
    return len(clean_text(tree.body.text(separator=" ") if tree.body else ""))


def _get_page(url: str) -> str | None:
    html = fetch.get_text(url)
    if html and _visible_text_len(html) >= MIN_TEXT_BEFORE_RENDER:
        return html
    rendered = fetch.render(url)
    return rendered or html


# Mailchimp keeps a public archive (and RSS) for every list with archiving on:
#   https://usN.campaign-archive.com/home/?u=<user>&id=<list>   (archive page)
#   https://usN.campaign-archive.com/feed?u=<user>&id=<list>    (RSS of issues)
# A site's "newsletter" link usually points at one issue (mailchi.mp/... or
# usN.campaign-archive.com/?u=..&id=<campaign>) whose footer links the archive.
_MC_ARCHIVE = re.compile(
    r"(us\d+)\.campaign-archive\.com/home/\?u=([0-9a-f]+)&(?:amp;)?id=([0-9a-f]+)", re.I)
_MC_ISSUE = re.compile(
    r"https?://(?:mailchi\.mp/[\w./-]+|us\d+\.campaign-archive\.com/\?[^\"'\s<>]+)", re.I)


def _is_feed_url(url: str) -> bool:
    low = url.lower()
    return ("campaign-archive.com/feed" in low or low.split("?")[0].endswith((".rss", ".xml", "/feed", "/feed/"))
            or "format=rss" in low)


def newsletter_feeds(html: str, follow: bool = True) -> list[str]:
    """RSS feeds of Mailchimp newsletter archives linked from a page.
    With `follow`, opens one linked issue to find its archive (1 request)."""
    found = {f"https://{m.group(1)}.campaign-archive.com/feed?u={m.group(2)}&id={m.group(3)}"
             for m in _MC_ARCHIVE.finditer(html)}
    if not found and follow:
        issue = _MC_ISSUE.search(html)
        if issue:
            page = fetch.get_text(issue.group(0).replace("&amp;", "&"))
            if page:
                found |= set(newsletter_feeds(page, follow=False))
    return sorted(found)


def _feeds_for(url: str, html: str) -> list[str]:
    tree = HTMLParser(html)
    feeds = []
    for n in tree.css('link[rel="alternate"]'):
        t = (n.attributes.get("type") or "").lower()
        href = n.attributes.get("href")
        if href and ("rss" in t or "atom" in t):
            if "comments" not in href:
                feeds.append(urljoin(url, href))
    root = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    low = html.lower()
    if "wp-content" in low:
        feeds.append(root + "/feed/")
    if "wix.com" in low or "wixstatic" in low:
        feeds.append(root + "/blog-feed.xml")
    if "squarespace" in low:
        feeds.append(url.split("?")[0] + "?format=rss")
    feeds += newsletter_feeds(html)
    seen, out = set(), []
    for f in feeds:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out[:4]


def _parse_feed(xml: str) -> list[dict]:
    try:
        root = ET.fromstring(xml.encode("utf-8", "ignore") if isinstance(xml, str) else xml)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        def child(*names):
            for c in it:
                if c.tag.split("}")[-1] in names:
                    return c
            return None
        title = child("title")
        link = child("link")
        body = child("encoded", "content", "description", "summary")
        pub = child("pubDate", "published", "updated", "date")
        href = ""
        if link is not None:
            href = (link.text or "").strip() or link.attrib.get("href", "")
        items.append({
            "title": clean_text(title.text if title is not None else ""),
            "url": href,
            "html": (body.text or "") if body is not None else "",
            "published": parse_dt(pub.text) if pub is not None and pub.text else None,
        })
    return items


def _wp_posts(url: str) -> list[dict]:
    root = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    r = fetch.get(root + "/wp-json/wp/v2/posts?per_page=25&_fields=title,link,content,date")
    if r is None:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    out = []
    for p in data if isinstance(data, list) else []:
        out.append({
            "title": clean_text((p.get("title") or {}).get("rendered", "")),
            "url": p.get("link", ""),
            "html": (p.get("content") or {}).get("rendered", ""),
            "published": parse_dt(p.get("date")),
        })
    return out


def _follow_links(url: str, html: str) -> list[str]:
    """Same-site links worth reading. Links that share the events page's own
    keyword come first: on a tag page like /tags/events-up-north the weekly
    roundup posts (/.../events-up-north-oct-12-18-...) beat the site's nav."""
    host = urlparse(url).netloc.lower()
    tree = HTMLParser(html)
    last = [seg for seg in urlparse(url).path.split("/") if seg]
    key = last[-1].lower() if last and len(last[-1]) > 5 else ""
    first, rest, seen = [], [], {url.rstrip("/")}
    for a in tree.css("a[href]"):
        href = urljoin(url, a.attributes.get("href") or "").split("#")[0]
        if not href.startswith("http") or urlparse(href).netloc.lower() != host:
            continue
        if _SKIP_HREF.search(href) or href.rstrip("/") in seen:
            continue
        label = clean_text(a.text())
        if key and key in href.lower():
            seen.add(href.rstrip("/"))
            first.append(href)
        elif _FOLLOW_HINT.search(href) or _FOLLOW_HINT.search(label):
            seen.add(href.rstrip("/"))
            rest.append(href)
    return (first + rest)[:MAX_FOLLOW]


# ── event extraction ─────────────────────────────────────────────────────────

def _html_text(html: str) -> str:
    tree = HTMLParser(html or "")
    for sel in ("script", "style", "noscript"):
        for n in tree.css(sel):
            n.decompose()
    node = tree.body or tree.root
    return clean_text(node.text(separator=" ")) if node else ""


_BLOCK_TAGS = ("p", "li", "td", "dd", "blockquote", "time")
_LEAF_TAGS = ("div", "span", "section", "article")
_HAS_BLOCK_CHILD = "p,div,li,ul,ol,table,section,article,h1,h2,h3,h4,h5"


def _blocks(html: str) -> list[tuple[str, str]]:
    """(nearest heading, block text) for every content block on a page.

    Headings are tracked so a "<h2>Title</h2><time>date</time>" pair, the most
    common hand-built listing, yields the heading as the title. Leaf divs (no
    block children) count as blocks so div-based listings aren't missed.
    """
    tree = HTMLParser(html)
    for sel in ("script", "style", "noscript", "nav", "footer", "form", "header", "aside"):
        for n in tree.css(sel):
            n.decompose()
    out, heading = [], ""
    body = tree.body or tree.root
    if body is None:
        return out
    for n in body.traverse(include_text=False):
        tag = n.tag
        if tag in ("h1", "h2", "h3", "h4", "h5"):
            heading = clean_text(n.text(separator=" "))
            out.append((heading, heading))
        elif tag in _BLOCK_TAGS or (tag in _LEAF_TAGS and n.css_first(_HAS_BLOCK_CHILD) is None):
            txt = clean_text(n.text(separator=" "))
            if 8 <= len(txt) <= 1200:
                out.append((heading, txt))
    # a leaf div and the <p>/<time> inside it can repeat the same text
    seen, uniq = set(), []
    for h, t in out:
        if (h, t) not in seen:
            seen.add((h, t))
            uniq.append((h, t))
    return uniq


_TIME_PREFIX = re.compile(
    r"^(?:[\s,:–—-]*(?:@|at|from)?\s*" + TIME_RE.pattern
    + r"(?:\s*(?:-|–|—|to)\s*" + TIME_RE.pattern.replace("?P<", "?P<x") + r")?)", re.I)
_WEAK_BEFORE = re.compile(r"^(?:join us|come|please join us|save the date|celebrate|"
                          r"mark your calendars?|coming|next|this|starting|beginning|"
                          r"\W*)(?:\s+(?:on|for|this))?$", re.I)
_JUNK_TITLE = re.compile(r"^\W|\b(call|phone|email|appointment|click|visit|see|contact)\b|\d{3}[-.]\d{4}", re.I)


def _title_from_line(text: str, ds: int, de: int, prev_end: int = 0) -> str:
    """Pull a title out of one line of listing text.

    'Oct 18 - Harvest Fest, 10am, Park' → title after the date (a separator
    follows it). 'Board Game Night Thursday, Feb 26 at 6 PM' → title before
    the date (nothing separates the date from what follows).
    """
    rest = _TIME_PREFIX.sub("", text[de:]).lstrip()
    after_sep = bool(re.match(r"^[:|–—-]", rest))
    after = re.sub(r"^[\s,:;|–—-]+", "", rest)
    after = re.split(r"\s*[,;(]\s*|\s+[–—-]\s+|\s[|•]\s", after)[0]
    after = _SENT_END.split(after)[0]
    after = re.sub(r"[\s,:;|–—-]+$", "", after)
    before = text[prev_end:ds]
    while True:  # "4:30pm 2p Yard Waste Site": strip every leading time
        stripped = _TIME_PREFIX.sub("", before, count=1)
        stripped = re.sub(r"^\s*(?:(?:3[01]|[12]\d|0?[1-9])\s+)+(?=\S)", "", stripped)  # grid day numbers
        if stripped == before:
            break
        before = stripped
    before = re.sub(r"[\s,:;|–—@-]+$", "", before).strip()
    before = _SENT_END.split(before)[-1] if before else ""
    before = re.sub(r"(?:\s+(?:on|is|are|will be|starts?|begins?|held|happens?))+$", "", before, flags=re.I)
    before = re.sub(r"^(?:the|our|annual)\s+", "", before, flags=re.I)
    if _WEAK_BEFORE.match(before):
        before = ""
    order = (after, before) if (after_sep or not before) else (before, after)
    for cand in order:
        cand = re.sub(r"^(?:(?:on|at|from|for|join us|the)\s+)+", "", cand, flags=re.I).strip()
        if 4 <= len(cand) <= 90 and re.search(r"[a-z]", cand, re.I) and not _JUNK_TITLE.search(cand):
            return cand[:90]
    return ""


_CHROME_HEADING = re.compile(r"follow us|up-to-date|for more (?:info|information)|"
                             r"stay (?:tuned|connected)|sign up|newsletter|subscribe|"
                             r"good times|cheers|welcome to", re.I)


_CHROME_TITLE = re.compile(r"follow us|up-to-date|for more (?:info|information)|"
                           r"sign up|newsletter|subscribe|cookies?|privacy", re.I)


_ROUNDUP_TITLE = re.compile(r"^(?:what[’']?s (?:happening|tappening|on|new|up)|"
                            r"(?:this|next) (?:week|month|weekend)\b|"
                            r"(?:weekly|monthly) (?:roundup|update|events)|events? roundup)", re.I)
_SENTENCE_VERB = re.compile(r"\b(?:is|are|was|were|will|can|may|should|must|available|"
                            r"please|you|your|we|our)\b", re.I)


def _title_ok(title: str) -> bool:
    if not title or len(title) < 4 or _GENERIC_TITLES.match(title) or _JUNK_TITLE.search(title):
        return False
    if _CHROME_TITLE.search(title) or _ROUNDUP_TITLE.match(title):
        return False
    words = title.split()
    if len(words) >= 7 and _SENTENCE_VERB.search(title):
        return False  # "Help with the application process is available ..." is prose
    # "6:00 pm - 11:00 pm", "Oct 24 | 7pm": nothing left once times/dates go
    rest = DATE_RE.sub(" ", TIME_RE.sub(" ", title))
    return len(re.findall(r"[A-Za-z]{2,}", rest)) >= 1 and not re.fullmatch(r"[\W\d\s]*(?:am|pm)?[\W\d\s]*", rest, re.I)


def _time_slice(text: str, prev_end: int, ds: int, de: int) -> str:
    """The bit of a multi-event block that holds *this* event's time:
    a time right after the date ("Mon, 12 Oct 2026 4:30pm"), else the first
    time within 40 chars after it ("Oct 25 - Parade, 5 pm"), else the last
    time between the previous event and this date ("4:30p Council Mon, 12 Oct")."""
    after = text[de:min(len(text), de + 40)]
    m = TIME_RE.search(after)
    if m:
        return after[m.start():m.end()]
    before = list(TIME_RE.finditer(text[prev_end:ds]))
    return before[-1].group(0) if before else ""


def _mk_event(source: Source, title: str, start: date, end: date | None,
              text: str, url: str, time_text: str | None = None) -> Event | None:
    title = clean_text(title)
    if not _title_ok(title):
        return None
    t = find_time(text if time_text is None else time_text)
    all_day = t is None
    start_dt = datetime(start.year, start.month, start.day, *(t or (0, 0)))
    end_dt = datetime(end.year, end.month, end.day) if end else None
    return Event(source_id=source.id, title=title, start=start_dt, end=end_dt,
                 all_day=all_day or bool(end), url=url, description=text[:600],
                 category="community", inferred=True)


def _from_post(source: Source, post: dict, today: date) -> list[Event]:
    """One announcement post = one event (its title, its first upcoming date).
    A roundup post ("What's Tappening - October") listing 3+ dates is parsed
    like a listing page instead, so each item keeps its own title."""
    title = post["title"]
    text = _html_text(post["html"])
    if not title or _NEG_TEXT.search(title):
        return []
    pub = post.get("published")
    ref = pub.date() if pub else today
    dates = find_dates(text, ref)
    if len({d[0] for d in dates}) >= 3:
        return _from_page(source, post["html"], post["url"] or source.target, today)
    signal_in_title = bool(_SIGNAL.search(title))
    for start, end, ds, de in dates:
        window = text[max(0, ds - 160):de + 160]
        if not (signal_in_title or _SIGNAL.search(window)):
            continue
        if _NEG_TEXT.search(window):
            continue
        if pub and start < pub.date() - timedelta(days=3):
            continue
        if not (today - timedelta(days=1) <= start <= today + timedelta(days=HORIZON_DAYS)):
            continue
        ev = _mk_event(source, title, start, end, text[max(0, ds - 200):], post["url"] or source.target)
        return [ev] if ev else []
    return []


def _date_only(text: str, today: date) -> date | None:
    """The date if `text` is nothing but a date ("Monday, Oct. 12"), else None."""
    if not text or len(text) > 40:
        return None
    ds = find_dates(text, today)
    if len(ds) != 1:
        return None
    start, _end, a, b = ds[0]
    rest = (text[:a] + text[b:]).strip(" ,.:-–—")
    return start if len(rest) <= 3 else None


def _from_page(source: Source, html: str, url: str, today: date) -> list[Event]:
    blocks = [(h, t) for h, t in _blocks(html)
              if not (t == h and len(t) > 120) and not _NEG_TEXT.search(t) and not _GRID_NOISE.search(t)]
    # a heading sitting over 3+ dated blocks is a section header ("Upcoming at
    # the Taproom"), not the name of each event under it
    dated_per_heading: dict[str, int] = {}
    for h, t in blocks:
        if h and t != h and find_dates(t, today):
            dated_per_heading[h] = dated_per_heading.get(h, 0) + 1
    events = []
    for heading, text in blocks:
        # Roundup layout: a date-only heading ("Monday, Oct. 12") over undated
        # items ("Chili cook-off - 11 a.m.-1 p.m. at Northern Pacific Center").
        if text != heading and not find_dates(text, today):
            hd = _date_only(heading, today)
            if hd:
                title = re.split(r"\s+[-–—]\s+|:\s", text, maxsplit=1)[0].strip()
                if " - " in text or " – " in text or " — " in text:
                    if today - timedelta(days=1) <= hd <= today + timedelta(days=HORIZON_DAYS):
                        ev = _mk_event(source, title, hd, None, text, url,
                                       time_text=text[len(title):len(title) + 40])
                        if ev:
                            events.append(ev)
            continue
        prev_end = 0
        for start, end, ds, de in find_dates(text, today):
            line_prev, prev_end = prev_end, de
            if not (today - timedelta(days=1) <= start <= today + timedelta(days=HORIZON_DAYS)):
                continue
            line_title = _title_from_line(text, ds, de, line_prev)
            use_heading = (heading and heading != text and _title_ok(heading)
                           and not _CHROME_HEADING.search(heading)
                           and dated_per_heading.get(heading, 0) < 3)
            title = heading if (use_heading and not (line_title and len(text) < 200)) else line_title
            if not title:
                continue
            if not (_SIGNAL.search(title) or _SIGNAL.search(text) or _FOLLOW_HINT.search(url)):
                continue
            # the time belongs to this event's own slice of the block, not to
            # whichever event in a multi-event block happens to come first
            ev = _mk_event(source, title, start, end, text, url,
                           time_text=_time_slice(text, line_prev, ds, de))
            if ev:
                events.append(ev)
    return events


class PostsAdapter:
    name = "posts"

    def scrape(self, source: Source) -> AdapterResult:
        today = date.today()
        target = source.target
        if _is_feed_url(target):
            # events_url is itself a feed (e.g. a Mailchimp newsletter archive):
            # every item is a post, roundups are parsed like listing pages
            xml = fetch.get_text(target)
            head = (xml or "").lstrip()[:300].lower()
            if "<rss" not in head and "<feed" not in head:
                return AdapterResult(self.name, detail="feed unreachable")
            found = []
            items = _parse_feed(xml)
            for p in items:
                found += _from_post(source, p, today)
            return self._finish(source, found, len(items))
        html = _get_page(target)
        if not html:
            return AdapterResult(self.name, detail="page unreachable")

        found: list[Event] = []
        docs = 1
        found += _from_page(source, html, target, today)
        if not found:
            # calendar widgets (DPCalendar, Timely, Elfsight...) fill in by JS
            rendered = fetch.render(target)
            if rendered:
                found += _from_page(source, rendered, target, today)

        posts = _wp_posts(target) if "wp-content" in html.lower() or "wp-json" in html.lower() else []
        for f in _feeds_for(target, html):
            xml = fetch.get_text(f)
            head = (xml or "").lstrip()[:300].lower()
            if "<rss" in head or "<feed" in head:
                posts += _parse_feed(xml)
        seen_urls = set()
        for p in posts:
            if p["url"] in seen_urls:
                continue
            seen_urls.add(p["url"])
            docs += 1
            found += _from_post(source, p, today)

        for link in _follow_links(target, html):
            page = fetch.get_text(link)
            if page:
                docs += 1
                found += _from_page(source, page, link, today)

        return self._finish(source, found, docs)

    def _finish(self, source: Source, found: list[Event], docs: int) -> AdapterResult:
        if source.category == "news":
            # Regional media (KAXE's "Events Up North") cover far beyond the
            # DMA (Bemidji, Brainerd). Keep an item only when it names a DMA
            # town, and use that town as the event's city.
            from adapters.exploremn import DMA_TOWNS
            from adapters.evvnt import _TOWN_STATE
            towns = sorted(set(DMA_TOWNS) | set(_TOWN_STATE), key=len, reverse=True)
            names = "|".join(re.escape(t) for t in towns)
            # (?!-): "chili cook-off" is not the town of Cook
            in_town = re.compile(r"\bin\s+(" + names + r")\b(?!-)", re.I)
            title_pat = re.compile(r"\b(" + names + r")\b(?!-)", re.I)
            kept = []
            for e in found:
                # the item's own "... at Venue in Town" (first 160 chars) beats a
                # town merely mentioned later in the description
                named = re.search(r"\bin ([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b", e.title)
                if named and not title_pat.fullmatch(named.group(1)):
                    continue   # "Family forest day in Bemidji": says where, and it's not here
                m = in_town.search(e.description[:160]) or title_pat.search(e.title)
                if m:
                    e.city = m.group(1).title()
                    e.state = _TOWN_STATE.get(m.group(1).lower(), "MN")
                    e.area = e.city
                    kept.append(e)
            found = kept
        # de-dup on (title, day); keep the richest copy
        uniq: dict[tuple, Event] = {}
        for e in found:
            k = (re.sub(r"[^a-z0-9]", "", e.title.lower()), e.start.date())
            if k not in uniq or len(e.description) > len(uniq[k].description):
                uniq[k] = e
        events = tag_from_source(sorted(uniq.values(), key=lambda e: e.start)[:MAX_EVENTS], source)
        return AdapterResult(self.name, events=events, ok=bool(events),
                             detail=f"{len(events)} inferred events from {docs} docs")

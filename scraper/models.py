"""Event model + normalization used across all adapters."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, timedelta, UTC
from typing import Optional

from dateutil import parser as dateparser

# Coarse category buckets surfaced as UI filters. Adapters map raw site
# categories/keywords onto these; unknown -> "other".
CATEGORIES = [
    "music",        # concerts, live music, DJ
    "arts",         # theater, gallery, film, literary
    "family",       # kid/family friendly, story time
    "food-drink",   # tastings, dinners, brewery/winery
    "sports-rec",   # races, tournaments, outdoor recreation
    "community",    # civic meetings, fundraisers, markets, fairs, festivals
    "education",    # classes, workshops, lectures
    "holiday",      # seasonal / holiday celebrations
    "other",
]

_WS = re.compile(r"\s+")

# Keyword -> category. Checked in this order; first hit wins, so put the more
# specific buckets before the catch-all "community". Multi-word phrases are
# matched as substrings; single tokens are matched on word boundaries.
_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("holiday", [
        "christmas", "halloween", "thanksgiving", "new year", "hanukkah", "easter",
        "4th of july", "fourth of july", "independence day", "valentine",
        "st. patrick", "st patrick", "tree lighting", "trunk or treat",
        "pumpkin patch", "santa", "holiday market", "holiday concert", "menorah",
    ]),
    ("music", [
        "concert", "live music", "orchestra", "symphony", "chorale",
        "acoustic", "open mic", "openmic", "jam session", "karaoke", "vinyl",
        "hip hop", "hip-hop", " jazz", "blues band", "bluegrass", "folk music", "punk",
        "metal show", "tribute band", "singer-songwriter", "songwriter", "recital",
        "porchfest", "music festival", "opera", "feat.", " & the ", "& friends",
        "live at", "live in the lounge",
    ]),
    ("arts", [
        "theatre", "theater", "musical", "playhouse", "gallery", "exhibit",
        "art opening", "art show", "artist reception", "film ", "movie", "cinema",
        "screening", "author", "poetry", "poems", "book club", "book signing",
        "literary", "storytelling", "dance ", "ballet", "improv", "comedy",
        "stand-up", "standup", "painting", "pottery", "watercolor", "printmaking",
        "sculpture", "plein air", "craft fair", "quilt", "photography exhibit",
        "art crawl", "art walk", "open studio",
    ]),
    ("family", [
        "story time", "storytime", "story hour", "kids ", "kid's", "children",
        "toddler", "preschool", "all ages", "family fun", "family-friendly",
        "petting zoo", "playground", "egg hunt", "lego", "puppet", "youth ",
        "teen ", "baby ", "sensory-friendly", "family day",
    ]),
    ("food-drink", [
        "dinner", "brunch", "luncheon", "tasting", "wine ", "winery", "beer ",
        "brewery", "brewing", "taproom", "cocktail", "happy hour", "food truck",
        "farmers market", "farmers' market", "farmer's market", "supper", "pancake",
        "chili ", "bbq", "barbecue", "cook-off", "cookoff", "bake sale", "brewfest",
        "wine walk", "distillery", "mead", "cider", "potluck", "fish fry",
        "oktoberfest", "beer garden", "tap takeover",
    ]),
    ("sports-rec", [
        "5k", "10k", "half marathon", "marathon", "fun run", "color run", "glow run",
        " run", "run/walk", "walk/run", " ride", "bike ride", "tournament",
        "hockey", "basketball", "soccer", " game", "baseball", "softball", "volleyball",
        "golf scramble", "golf outing", "golf tournament", "disc golf", "pickleball",
        "ski ", "skiing", "snowshoe", "fat bike", "paddle", "kayak", "canoe", "hike",
        "fishing", "regatta", "yoga", "zumba", "bootcamp", "fitness", "workout",
        "rec league", "climbing", "curling", "derby", "race ",
    ]),
    ("education", [
        "workshop", "class", "seminar", "lecture", "training", "webinar",
        "info session", "informational meeting", "tutorial", "how to",
        "how-to", "certification", "course", "lesson", "presentation",
        "learn to", "demonstration", "info night", "orientation", "book talk",
    ]),
    ("community", [
        "festival", "fair", "parade", "fundraiser", "fund raiser", "benefit ",
        "gala", "market", "city council", "county board", "planning commission",
        "town board", "village board", "school board", " meeting", "board of",
        "committee", "public hearing", "forum", "celebration", "pride", "cleanup",
        "clean-up", "blood drive", "ceremony", "open house", "grand opening",
        "ribbon cutting", "town hall", "rummage", "garage sale", "flea market",
        "craft show", "vendor", "expo", "conference", "networking", "chamber",
        "volunteer", "clinic", "food shelf", "food drive", "auction", "raffle",
        "commission", "council", "caucus",
    ]),
]
_WORD_KEYS = {kw for _, kws in _CATEGORY_RULES for kw in kws if " " not in kw and not kw.endswith(" ") and not kw.startswith(" ")}
_WORD_RE = {kw: re.compile(rf"\b{re.escape(kw)}\b", re.I) for kw in _WORD_KEYS}


def _match(hay: str) -> Optional[str]:
    for cat, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if kw in _WORD_RE:
                if _WORD_RE[kw].search(hay):
                    return cat
            elif kw in hay:
                return cat
    return None


def infer_category(title: str, description: str = "", fallback: str = "other") -> str:
    """Guess a category bucket from an event's text; return `fallback` if nothing hits.

    The title is checked first (high confidence); the description is only
    consulted when the title gives nothing.
    """
    hit = _match(f" {(title or '').lower()} ")
    if hit:
        return hit
    hit = _match(f" {(description or '').lower()[:400]} ")
    if hit:
        return hit
    return fallback if fallback in CATEGORIES else "other"


# ── description cleanup ───────────────────────────────────────────────────────
# Every adapter's description passes through clean_description() (from
# Event.normalize), so junk is fixed in one place:
#   - HTML -> text with <style>/<script> CONTENTS dropped (Reif Center's page
#     builder puts CSS inside the description; stripping tags alone kept it);
#   - leftover CSS rule blobs ".x #y a{color:#fff;}" removed;
#   - page boilerplate labels (SPONSORED BY, KNOW BEFORE YOU GO, Read More,
#     Add to Calendar, ...) removed - matched only in label form, so a sentence
#     like "buy tickets at the door" is never touched;
#   - a description that just repeats the title first loses that copy;
#   - long text ends at a sentence break with "…", never mid-word.
DESC_MAX = 1200

# Text that says an event repeats. A timed event that carries it and spans
# more than SERIES_SPAN is a series published as one block (first class ->
# last class), which the calendar draws as a week-long bar.
_SERIES_TEXT = re.compile(
    r"\b(?:recurring|recurs|weekly|bi-?weekly|monthly|every\s+(?:other\s+)?(?:week|month|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)days?))\b", re.I)
SERIES_SPAN = timedelta(days=6)

_CSS_RULE = re.compile(
    r"(?:[#.@][\w-][^{}<>]{0,300}|\b(?:a|p|div|span|body|html|h[1-6]|li|ul|ol|img|table|td|tr|section|button)\b[^{}<>]{0,300})"
    r"\{[^{}]{0,1200}\}")
_CSS_LEFTOVER = re.compile(r"(?:[\w-]+\s*:\s*[^;{}]{1,80};\s*){2,}")   # "color: #fff; font-size: 13px;"
_BOILERPLATE = re.compile(
    r"(?:^|(?<=[\s.!?:|]))(?:"
    r"SPONSORED BY|Sponsored [Bb]y:|PRESENTED BY:|KNOW BEFORE YOU GO|Know Before You Go|ABOUT THE (?:SHOW|EVENT|ARTIST)S?|"
    r"EVENT DETAILS|TICKETS(?= [A-Z])|BUY TICKETS|Buy Tickets|GET TICKETS|Get Tickets|"
    # link labels: Title Case / CAPS anywhere; lowercase only with an arrow or
    # at the very end ("Read more about the band" is a sentence, not a label)
    r"Read More(?: »| ›| →|\.\.\.|…)?|READ MORE|Learn More(?: »| ›| →)?|LEARN MORE|More Info(?:rmation)?(?: »)?|"
    r"(?:Read more|Learn more|More info|Continue reading)(?:\s*(?:»|›|→|\.\.\.|…)|\s*$)|"
    r"Continue Reading(?: »| →)?|Click [Hh]ere(?: for [Mm]ore [Ii]nfo(?:rmation)?| to [Rr]egister)?|"
    r"Add to (?:Google )?[Cc]alendar|Google Calendar|iCalendar|Outlook 365|Outlook Live|"
    r"Export \.ics [Ff]ile|Export to Outlook|Share [Tt]his [Ee]vent|View on [Mm]ap|"
    r"See (?:the )?flyer below(?: for additional information)?\.?"
    r")(?=$|[\s.!?:|»›→])")
_SENT_END = re.compile(r"[.!?…][\"”')\]]?(?=\s|$)")


def _html_to_text(value: str) -> str:
    if "<" not in value or not re.search(r"</?[a-zA-Z][^>]*>", value):
        return value
    from selectolax.parser import HTMLParser
    tree = HTMLParser(f"<div>{value}</div>")
    for sel in ("style", "script", "noscript", "svg", "template", "iframe"):
        for n in tree.css(sel):
            n.decompose()
    node = tree.body or tree.root
    return node.text(separator=" ") if node else value


def truncate_desc(text: str, limit: int = DESC_MAX) -> str:
    """Cut at a sentence break (or a word break) with '…', never mid-word."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    ends = [m.end() for m in _SENT_END.finditer(head)]
    if ends and ends[-1] >= limit * 0.5:
        return head[:ends[-1]].rstrip()
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,;:-–—") + "…"


def clean_description(value: Optional[str], title: str = "", limit: int = DESC_MAX) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))
    text = _html_to_text(text)
    text = html.unescape(text)
    for _ in range(4):                       # nested/adjacent rules peel off in passes
        new = _CSS_RULE.sub(" ", text)
        if new == text:
            break
        text = new
    text = _CSS_LEFTOVER.sub(" ", text)
    text = _BOILERPLATE.sub(" ", text)
    text = clean_text(text)
    text = re.sub(r"^[\s|:•·–—-]+", "", text)
    # "Wilson Fairchild Wilson Fairchild is more than..." -> drop the title copy
    if title and len(title) >= 4:
        t = re.escape(clean_text(title))
        # only a clear copy: "Title - ...", "Title: ...", or "Title Title is ..."
        stripped = re.sub(rf"^\s*{t}\s*(?:[-–—:|•]\s*|(?={t}\b))", "", text, count=1, flags=re.I)
        if len(stripped) >= 20 and stripped != text:
            text = stripped[0].upper() + stripped[1:]
    text = truncate_desc(text, limit)
    # a source that cut its own excerpt mid-word ("...The Statler Brothers w")
    if len(text) >= 150 and not re.search(r"[.!?…\"”')\]]$", text):
        ends = [m.end() for m in _SENT_END.finditer(text)]
        if ends and ends[-1] >= len(text) * 0.6:
            text = text[:ends[-1]]
        else:
            text = text.rsplit(" ", 1)[0].rstrip(" ,;:-–—") + "…"
    return text


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("​", "").replace("\xad", "")
    return _WS.sub(" ", value).strip()


_CENTRAL = None


def _to_local_naive(dt: datetime) -> datetime:
    """Aware -> America/Chicago wall time, then drop tzinfo. A value with an
    offset is an instant; "2026-12-09T13:45:00Z" is 7:45 AM here, not 13:45.
    (Until 2026-10-11 the offset was simply stripped, which put every
    UTC-stamped ChamberMaster/JSON-LD event 5-6 hours late.) A value already
    in local offset ("...-05:00" in summer) is unchanged."""
    global _CENTRAL
    if dt.tzinfo is None:
        return dt
    if _CENTRAL is None:
        from zoneinfo import ZoneInfo
        _CENTRAL = ZoneInfo("America/Chicago")
    return dt.astimezone(_CENTRAL).replace(tzinfo=None)


def from_epoch(seconds: float) -> datetime:
    """Unix time -> naive America/Chicago wall time. Never use a bare
    datetime.fromtimestamp(): it uses the machine's zone, which is Central on
    the owner's PC but UTC on GitHub Actions (events 5-6 h off in CI)."""
    from datetime import timezone as _tz
    return _to_local_naive(datetime.fromtimestamp(seconds, tz=_tz.utc))


def parse_dt(value, *, default_tz=None, dayfirst=False) -> Optional[datetime]:
    """Best-effort parse of a date/datetime string or object.

    Always returns a naive datetime (wall-clock time as published) — every
    adapter and Event.normalize() assumes naive throughout, and a source that
    mixes offset-bearing and offset-free strings (JSON-LD startDate/endDate is
    a repeat offender) would otherwise produce a naive/aware pair that crashes
    the < comparison in normalize().
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _to_local_naive(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    try:
        dt = dateparser.parse(str(value), dayfirst=dayfirst, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None
    return _to_local_naive(dt) if dt else dt


@dataclass
class Event:
    source_id: str
    title: str
    start: datetime
    end: Optional[datetime] = None
    all_day: bool = False
    venue: str = ""
    city: str = ""
    state: str = ""          # MN | WI | MI
    area: str = ""            # filter label inherited from the source, adapters may override
    address: str = ""
    url: str = ""             # canonical link to the event detail page
    description: str = ""
    category: str = "other"
    price: str = ""           # free-form ("Free", "$10", "$5-$20")
    image: str = ""
    # True when the adapter inferred this event from free text (news posts,
    # page prose) rather than a structured calendar. A single-source inferred
    # event always goes to the review queue, whatever the source's trust tier.
    inferred: bool = False
    scraped_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"))

    def normalize(self) -> "Event":
        self.title = clean_text(self.title)
        self.venue = clean_text(self.venue)
        self.city = clean_text(self.city)
        self.area = clean_text(self.area)
        self.address = clean_text(self.address)
        self.description = clean_description(self.description, self.title)
        self.price = clean_text(self.price)
        if self.category not in CATEGORIES:
            self.category = "other"
        # Adapters that can't determine a real category leave "other" or a coarse
        # "community" default — refine those from the event text.
        if self.category in ("other", "community"):
            self.category = infer_category(self.title, self.description, fallback=self.category)
        if self.end and self.end < self.start:
            self.end = None
        if self.looks_like_series_span():
            self.end = None          # keep the first occurrence, not a bar to the last
        return self

    def looks_like_series_span(self) -> bool:
        """A repeating event whose end is the series' last date, not this
        occurrence's. Adapters should expand the series; this is the backstop."""
        return bool(self.end and self.start and not self.all_day
                    and self.end - self.start > SERIES_SPAN
                    and _SERIES_TEXT.search(f"{self.title}\n{self.description}"))

    @property
    def fingerprint(self) -> str:
        """Stable identity for cross-source dedup: same title + same day + same city."""
        key = "|".join([
            re.sub(r"[^a-z0-9]", "", self.title.lower()),
            self.start.date().isoformat() if self.start else "",
            re.sub(r"[^a-z0-9]", "", self.city.lower()),
        ])
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def is_valid(self) -> bool:
        return bool(self.title) and isinstance(self.start, datetime)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat() if self.start else None
        d["end"] = self.end.isoformat() if self.end else None
        d["fingerprint"] = self.fingerprint
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Optional["Event"]:
        start = parse_dt(d.get("start"))
        if not start:
            return None
        ev = cls(source_id=d.get("source_id", ""), title=d.get("title", ""), start=start)
        ev.end = parse_dt(d.get("end"))
        for f in ("all_day", "venue", "city", "state", "area", "address", "url",
                  "description", "category", "price", "image", "inferred"):
            if d.get(f) is not None:
                setattr(ev, f, d[f])
        return ev

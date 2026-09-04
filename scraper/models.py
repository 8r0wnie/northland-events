"""Event model + normalization used across all adapters."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, UTC
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


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("​", "").replace("\xad", "")
    return _WS.sub(" ", value).strip()


def parse_dt(value, *, default_tz=None, dayfirst=False) -> Optional[datetime]:
    """Best-effort parse of a date/datetime string or object."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    try:
        return dateparser.parse(str(value), dayfirst=dayfirst, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None


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
    scraped_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"))

    def normalize(self) -> "Event":
        self.title = clean_text(self.title)
        self.venue = clean_text(self.venue)
        self.city = clean_text(self.city)
        self.area = clean_text(self.area)
        self.address = clean_text(self.address)
        self.description = clean_text(self.description)[:2000]
        self.price = clean_text(self.price)
        if self.category not in CATEGORIES:
            self.category = "other"
        # Adapters that can't determine a real category leave "other" or a coarse
        # "community" default — refine those from the event text.
        if self.category in ("other", "community"):
            self.category = infer_category(self.title, self.description, fallback=self.category)
        if self.end and self.end < self.start:
            self.end = None
        return self

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
                  "description", "category", "price", "image"):
            if d.get(f) is not None:
                setattr(ev, f, d[f])
        return ev

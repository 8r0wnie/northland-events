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

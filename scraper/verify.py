"""Cross-source confirmation + the admin review queue.

After every active source is scraped, events are grouped by "same real-world
event" (fuzzy title + date + place). A merged event is **confirmed** when it has
two or more independent sources, or one source from a trusted origin. A merged
event with a single low-trust source (aggregators, hard-to-verify sites) is held
for admin review — it stays out of the public calendar until a decision in
`moderation/decisions.csv` green-lights or drops it.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz

from models import Event
from registry import Source

DECISIONS_PATH = Path(__file__).resolve().parent.parent / "moderation" / "decisions.csv"

TITLE_RATIO = 78          # rapidfuzz token_set_ratio threshold for "same event"
DAY_SLACK = 1             # events up to this many days apart can still be the same


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _same_event(a: Event, b: Event) -> bool:
    if abs((a.start.date() - b.start.date()).days) > DAY_SLACK:
        return False
    if fuzz.token_set_ratio(_norm(a.title), _norm(b.title)) < TITLE_RATIO:
        return False
    ca, cb = _norm(a.city), _norm(b.city)
    va, vb = _norm(a.venue), _norm(b.venue)
    if ca and cb and ca != cb:
        # different cities is only OK when venues clearly match
        return bool(va and vb and fuzz.partial_ratio(va, vb) >= 85)
    if va and vb and fuzz.token_set_ratio(va, vb) < 45:
        return False
    return True


@dataclass
class MergedEvent:
    event: Event
    source_ids: list[str] = field(default_factory=list)
    source_links: list[dict] = field(default_factory=list)  # [{name, url}]

    @property
    def key(self) -> str:
        return self.event.fingerprint


def _merge_into(base: Event, other: Event) -> None:
    """Fill blanks on `base` from `other` (base is the higher-trust representative)."""
    for fld in ("venue", "address", "city", "description", "image", "price", "url"):
        if not getattr(base, fld) and getattr(other, fld):
            setattr(base, fld, getattr(other, fld))
    if not base.end and other.end:
        base.end = other.end
    if base.category in ("other", "community") and other.category not in ("other", "community"):
        base.category = other.category


def group_events(events: list[Event], sources: dict[str, Source]) -> list[MergedEvent]:
    """Cluster events that describe the same real-world event."""
    trust_of = {sid: s.trust_tier for sid, s in sources.items()}
    name_of = {sid: s.name for sid, s in sources.items()}
    url_of = {sid: (s.events_url or s.url) for sid, s in sources.items()}

    # sort so a trusted, data-rich event is the cluster representative
    def rep_rank(e: Event) -> tuple:
        richness = sum(bool(x) for x in (e.description, e.venue, e.image, e.price, e.end))
        return (0 if trust_of.get(e.source_id) == "high" else 1, -richness)

    events = sorted(events, key=lambda e: (e.start, *rep_rank(e)))

    # index merged clusters by day so each new event only compares against the
    # handful of clusters within +/-1 day
    from collections import defaultdict
    by_day: dict[int, list[MergedEvent]] = defaultdict(list)
    merged: list[MergedEvent] = []

    for e in events:
        ord_ = e.start.date().toordinal()
        placed = False
        for day in (ord_ - 1, ord_, ord_ + 1):
            for m in by_day.get(day, ()):
                if _same_event(m.event, e):
                    if e.source_id not in m.source_ids:
                        m.source_ids.append(e.source_id)
                        m.source_links.append({
                            "name": name_of.get(e.source_id, e.source_id),
                            "url": e.url or url_of.get(e.source_id, ""),
                        })
                    _merge_into(m.event, e)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            m = MergedEvent(
                event=e,
                source_ids=[e.source_id],
                source_links=[{"name": name_of.get(e.source_id, e.source_id),
                               "url": e.url or url_of.get(e.source_id, "")}],
            )
            merged.append(m)
            by_day[ord_].append(m)
    return merged


def classify(m: MergedEvent, sources: dict[str, Source]) -> str:
    """'confirmed' | 'needs-review'."""
    distinct = set(m.source_ids)
    if len(distinct) >= 2:
        return "confirmed"
    if any(sources.get(sid) and sources[sid].trust_tier == "high" for sid in distinct):
        return "confirmed"
    return "needs-review"


# ── moderation decisions ─────────────────────────────────────────────────────
def load_decisions(path: Path = DECISIONS_PATH) -> dict[str, str]:
    """key -> 'approve' | 'reject'."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("key") or "").strip()
            dec = (row.get("decision") or "").strip().lower()
            if key and dec in ("approve", "reject"):
                out[key] = dec
    return out


def append_decisions_seen(queue: list[dict], decisions: dict[str, str],
                          path: Path = DECISIONS_PATH) -> None:
    """Make sure every currently-queued key has a blank row in decisions.csv so
    the admin can edit it in place (and so resolved items keep their record)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_keys = set()
    rows: list[dict] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append(row)
                if row.get("key"):
                    existing_keys.add(row["key"].strip())
    added = 0
    for item in queue:
        if item["key"] not in existing_keys:
            rows.append({"key": item["key"], "decision": "",
                         "title": item["title"], "date": item["start"][:10],
                         "source": item["source"]["name"], "note": ""})
            existing_keys.add(item["key"])
            added += 1
    if added or not path.exists():
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["key", "decision", "title", "date", "source", "note"])
            w.writeheader()
            w.writerows(rows)

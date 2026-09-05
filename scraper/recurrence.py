"""Recurring-event tracking + regional trend stats.

`events.json` is a rolling ~200-day window, overwritten every run — it has no
memory. This module gives the project one: every run, each published event is
filed under a *series key* (its identity across years, ignoring the specific
date/year) into a persistent, ever-growing history store. A series becomes
"confirmed recurring" once it's been seen in 2+ distinct years; before that,
it can still be flagged "likely recurring" from context clues already present
in a single year's listing — most annual events say so themselves ("45th
Annual Scandinavian Festival" doesn't need five decades of scrape history to
believe).

Two files come out of this, both under site/data/ (fetched by the private
recurring.html dashboard the same way tabling.html reads events.json — same
"unlinked + passphrase" privacy model, not actual data secrecy):

  event_history.json     the growing archive (source of truth, never trimmed)
  recurring_events.json  precomputed, UI-ready view (regenerated each run)

A third small file, scrape_stats_history.json, is a capped day-by-day time
series (published/queued/category counts) for the "how is coverage trending"
half of the dashboard.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

STATS_HISTORY_MAX_DAYS = 730  # ~2 years of daily snapshots is plenty for a trend line

_ORDINAL_ANNUAL = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th)\s+annual\b", re.I)
_ANNUAL_WORD = re.compile(r"\bannual\b", re.I)
_YEAR_TOKEN = re.compile(r"\b(19|20)\d\d\b")
_NOISE_WORDS = re.compile(
    r"\b(the|annual|presents?|is|back|returns?|celebrat\w*|edition)\b", re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def series_key(title: str, area: str = "") -> str:
    """Identity for the same event across years — same title stripped of the
    parts that change year to year (a leading "Nth Annual", a trailing year,
    generic filler words), plus a coarse location. Two titles that normalize
    to the same key are treated as the same recurring series.

    The location component matters: a generic name like "Fall Festival" is
    common enough that two unrelated small towns can each run one. Without
    disambiguating by area, next year's Ashland Fall Festival and this year's
    Ironwood Fall Festival would merge into one fictitious two-year series.
    Area (not the more specific venue) is the right grain here — the same
    real event's venue occasionally changes year to year, but it doesn't
    jump to a different part of the region."""
    t = _ORDINAL_ANNUAL.sub(" ", title or "")
    t = _YEAR_TOKEN.sub(" ", t)
    t = t.lower()
    t = _PUNCT.sub(" ", t)
    t = _NOISE_WORDS.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    area_key = _PUNCT.sub(" ", (area or "").lower())
    area_key = _WS.sub(" ", area_key).strip()
    return f"{t}|{area_key}"


def annual_signal(title: str) -> dict:
    """Context-clue evidence, from the title alone, that this is a recurring
    event — usable the very first time we ever see it, before any history
    has accumulated."""
    title = title or ""
    reasons: list[str] = []
    claimed_number = None
    m = _ORDINAL_ANNUAL.search(title)
    if m:
        claimed_number = int(m.group(1))
        reasons.append(f'title says "{m.group(0)}"')
    elif _ANNUAL_WORD.search(title):
        reasons.append('title says "annual"')
    return {"reasons": reasons, "claimed_annual_number": claimed_number}


_EDITION_GAP_DAYS = 21  # dates within this many days of each other count as one "edition"


def _edition_cluster_count(dates: list[date]) -> int:
    """How many separate editions these dates represent, within one snapshot.

    A multi-day fair (or a festival plus its own preview event a couple weeks
    out) produces several date entries that all belong to the *same* edition
    — clustering them prevents that from being mistaken for high frequency.
    Genuinely separate editions inside one 200-day window (monthly meetup,
    weekly class) fall in different clusters."""
    ds = sorted(set(dates))
    if not ds:
        return 0
    clusters = 1
    for a, b in zip(ds, ds[1:]):
        if (b - a).days > _EDITION_GAP_DAYS:
            clusters += 1
    return clusters


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def update_history(published: list[dict], history_path: Path) -> dict:
    """Merge this run's published events into the persistent history store.

    One occurrence is kept per (series, year) — a future event sitting in the
    200-day window gets scraped again every day until it happens, but it only
    ever records once per year here, not once per day it was visible.
    A "how often does this actually happen" guard runs first: a real annual
    event (Carlton Daze, a Blueberry Festival) can only show up as ONE
    cluster of nearby dates in any single 200-day snapshot — its next edition
    is ~a year away, outside the window. A book club or a ballet class shows
    up as MANY separate clusters spread across the same snapshot. That single
    observation is enough to tell them apart today, without waiting on
    cross-year spacing data — series that clear it are marked `too_frequent`
    and are never surfaced as a candidate annual event, no matter what their
    title claims or how their cross-year dates later happen to fall.
    """
    store = _load_json(history_path, {"series": {}})
    series_map: dict = store.setdefault("series", {})
    new_series = 0
    new_occurrences = 0

    dates_by_key: dict[str, list[date]] = {}
    for e in published:
        title, start = e.get("title") or "", e.get("start") or ""
        if not title or not start:
            continue
        key = series_key(title, e.get("area", ""))
        if not key:
            continue
        try:
            dates_by_key.setdefault(key, []).append(date.fromisoformat(start[:10]))
        except ValueError:
            continue
    frequent_keys = {key for key, ds in dates_by_key.items() if _edition_cluster_count(ds) >= 2}

    for e in published:
        title = e.get("title") or ""
        start = e.get("start") or ""
        if not title or not start:
            continue
        key = series_key(title, e.get("area", ""))
        if not key:
            continue
        year = start[:4]
        try:
            int(year)
        except ValueError:
            continue

        entry = series_map.get(key)
        if entry is None:
            entry = {"title": title, "venue": e.get("venue", ""), "city": e.get("city", ""),
                      "area": e.get("area", ""), "category": e.get("category", ""),
                      "occurrences": {}, "too_frequent": False}
            series_map[key] = entry
            new_series += 1
        # keep the freshest metadata (venue/category can be filled in later by a richer source)
        entry["title"] = title
        entry["venue"] = e.get("venue") or entry.get("venue", "")
        entry["city"] = e.get("city") or entry.get("city", "")
        entry["area"] = e.get("area") or entry.get("area", "")
        entry["category"] = e.get("category") or entry.get("category", "")
        # sticky once tripped — a series that ever showed a weekly/monthly
        # cadence doesn't get to re-qualify just because a later snapshot
        # happened to catch it during a quiet stretch
        entry["too_frequent"] = entry.get("too_frequent", False) or key in frequent_keys

        occ = entry["occurrences"].setdefault(year, {})
        if not occ:
            new_occurrences += 1
        occ.update({
            "date": start[:10],
            "title": title,
            "source_count": e.get("source_count", 1),
            "url": e.get("url", ""),
        })

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(store, indent=1), encoding="utf-8")
    return {"series_total": len(series_map), "new_series": new_series, "new_occurrences": new_occurrences}


def _predict_next(occurrences: dict) -> str | None:
    """Naive next-occurrence estimate: same month/day as the most recent
    occurrence, one year later. Good enough for "plan around roughly this
    time" — not a real date until a second year confirms the pattern holds."""
    if not occurrences:
        return None
    latest_year = max(occurrences)
    latest = occurrences[latest_year]
    try:
        d = date.fromisoformat(latest["date"])
    except ValueError:
        return None
    try:
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:  # Feb 29 on a non-leap year+1
        return d.replace(year=d.year + 1, day=28).isoformat()


_MIN_ANNUAL_GAP_DAYS = 300   # loose enough for an event that shifts a few weeks year to year
_MAX_ANNUAL_GAP_DAYS = 400   # tight enough to exclude "just happened to cross a Dec/Jan boundary"


def _has_annual_spacing(occurrences: dict) -> bool:
    """True gap between two occurrences must actually read as 'about a year
    apart' — two distinct calendar-year *labels* aren't enough on their own.
    A weekly meetup that happens to have one instance on Dec 28 and another
    on Jan 31 carries two different year labels but is 34 days apart, not a
    year; a real annual festival a scrape window catches twice (once as it's
    approaching, once after it repeats) will be ~350-380 days apart."""
    dates = sorted(date.fromisoformat(o["date"]) for o in occurrences.values())
    return any(_MIN_ANNUAL_GAP_DAYS <= (b - a).days <= _MAX_ANNUAL_GAP_DAYS
               for a, b in zip(dates, dates[1:]))


def build_recurring_view(history: dict) -> dict:
    """Precompute the UI-ready recurring-events list from the raw history store."""
    confirmed, likely = [], []
    for key, entry in history.get("series", {}).items():
        if entry.get("too_frequent"):
            continue  # a meetup/class, however titled — never an annual-event candidate
        occurrences = entry.get("occurrences", {})
        years = sorted(occurrences)
        signal = annual_signal(entry.get("title", ""))
        row = {
            "key": key,
            "title": entry.get("title", ""),
            "venue": entry.get("venue", ""),
            "city": entry.get("city", ""),
            "area": entry.get("area", ""),
            "category": entry.get("category", ""),
            "years_seen": years,
            "occurrence_count": len(years),
            "claimed_annual_number": signal["claimed_annual_number"],
            "signal_reasons": signal["reasons"],
            "last_date": occurrences[years[-1]]["date"] if years else None,
            "last_url": occurrences[years[-1]]["url"] if years else None,
            "predicted_next": _predict_next(occurrences),
        }
        if len(years) >= 2 and _has_annual_spacing(occurrences):
            confirmed.append(row)
        elif signal["reasons"]:
            likely.append(row)
        # everything else (multi-year but not annually spaced, or single
        # occurrence with no context clue) -> not shown; too weak a claim

    confirmed.sort(key=lambda r: r["predicted_next"] or "")
    likely.sort(key=lambda r: r["predicted_next"] or "")
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "confirmed": confirmed,
        "likely": likely,
    }


def append_stats_snapshot(stats_path: Path, snapshot: dict) -> None:
    """Append today's run stats to the capped daily time series, replacing
    any existing entry for the same date (so a manual re-run doesn't double
    up)."""
    history = _load_json(stats_path, [])
    today = snapshot["date"]
    history = [row for row in history if row.get("date") != today]
    history.append(snapshot)
    history.sort(key=lambda r: r["date"])
    if len(history) > STATS_HISTORY_MAX_DAYS:
        history = history[-STATS_HISTORY_MAX_DAYS:]
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(history, indent=1), encoding="utf-8")

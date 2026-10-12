"""Northland Events scraper — orchestrator.

Usage:
  python main.py triage [--only id,id]      probe every source, report what yields events
  python main.py scrape [--only id,id]      run active sources, write site/data/events.json
  python main.py refresh-local              scrape `refresh: local` sources into the cache only
  python main.py list                       print the registry

Run from the scraper/ directory.
"""
from __future__ import annotations

import argparse
import os
import re
import json
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

try:  # Windows consoles default to cp1252 and choke on arrows/box chars
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import fetch
from registry import load_sources, Source
from adapters import GENERIC, BY_PLATFORM
from models import Event
from verify import group_events, classify, review_reason, load_decisions
from tabling import classify_tabling
import recurrence

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "site" / "data"
REPORT_DIR = ROOT / "scraper" / "output"
CACHE_PATH = REPORT_DIR / "source_cache.json"
HISTORY_PATH = DATA_DIR / "event_history.json"
SOURCE_HISTORY_PATH = REPORT_DIR / "source_history.json"
SOURCES_VIEW_PATH = DATA_DIR / "sources.json"
ANNUAL_DECISIONS_PATH = ROOT / "moderation" / "annual_decisions.csv"
PROMOTE_WINDOW, PROMOTE_MIN_RUNS = 7, 5   # potential -> source after events on 5 of the last 7 runs
RECURRING_PATH = DATA_DIR / "recurring_events.json"
STATS_HISTORY_PATH = DATA_DIR / "scrape_stats_history.json"

HORIZON_DAYS = 200
CACHE_MAX_AGE_DAYS = 6      # carry a source's last-good events forward this long
LOCAL_CACHE_MAX_AGE_DAYS = 10  # refresh: local sources — CI can't reach them, so the
                               # owner's machine refreshes the cache; allow for missed days


def adapters_for(source: Source):
    """Pinned adapters, in order. `platform:` may chain several with commas
    (e.g. "govoffice,posts" = the calendar first, news-post text as fallback)."""
    if source.platform:
        pinned = [BY_PLATFORM[p.strip()] for p in source.platform.split(",")
                  if p.strip() in BY_PLATFORM]
        if pinned:
            return pinned
    if source.effective_tier == "potential":
        # an owner-added site nobody has triaged: try every cheap adapter,
        # then read it as text (its events all go to review anyway)
        return GENERIC + [BY_PLATFORM["posts"]]
    return GENERIC


def scrape_source(source: Source) -> tuple[list[Event], str]:
    errors = []
    for adapter in adapters_for(source):
        try:
            result = adapter.scrape(source)
        except Exception as exc:  # noqa: BLE001 — one broken site must never end the run
            errors.append(f"{adapter.name} crashed: {type(exc).__name__}: {str(exc)[:80]}")
            print(f"    ! {errors[-1]}")
            continue
        if result.ok and result.events:
            return result.events, f"{adapter.name}: {result.detail}"
    return [], "no adapter produced events" + (f" ({'; '.join(errors)})" if errors else "")


# Calendar entries that aren't public events: closures and staff-only items
# ("NWLS Closed", "Horizon Circulation Training for Library Staff").
NOT_PUBLIC = re.compile(
    r"^\W*(?:[\w&.'-]+\s+){0,4}closed\W*$|\bclosed for\b|\b(?:office|library|offices) (?:is |are )?closed\b"
    r"|\bstaff (?:only|training|meeting|in-?service)\b|\bfor (?:library |city )?staff\b"
    r"|\bno school\b|\bholiday\s*-\s*closed\b", re.I)

FREQUENT_MIN_COUNT = 20    # same title this often in the window ≈ weekly or more
FREQUENT_SERIES_DAYS = 60   # ...then show only its next 60 days (rolling)


def trim_frequent(events: list[Event], now: datetime) -> list[Event]:
    """Project rule: a source's high-frequency series (daily/weekly programs
    like open gym or a walking club) shows only the next FREQUENT_SERIES_DAYS,
    so it can't swamp one-off events. Applies to every adapter, including
    those whose platform pre-expands recurrence (DPCalendar, Revize, ...)."""
    from collections import Counter
    counts = Counter(e.title.strip().lower() for e in events)
    cutoff = now + timedelta(days=FREQUENT_SERIES_DAYS)
    return [e for e in events
            if counts[e.title.strip().lower()] < FREQUENT_MIN_COUNT or e.start <= cutoff]


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")


def _age_days(entry: dict) -> float:
    when = datetime.fromisoformat(entry["when"])
    return (datetime.now() - when).total_seconds() / 86400


def cmd_scrape(args) -> None:
    sources = load_sources()
    if args.only:
        wanted = set(args.only.split(","))
        sources = [s for s in sources if s.id in wanted]
    else:
        sources = [s for s in sources if s.status == "active"]

    if not sources:
        print("No matching sources (need status: active, or pass --only).")
        return

    all_events: list[Event] = []
    report = []
    now = datetime.now()
    horizon = now + timedelta(days=HORIZON_DAYS)

    cache = _load_cache()
    carried = 0

    for s in sources:
        print(f"→ {s.id} ({s.name})")
        if s.refresh == "local" and os.environ.get("CI"):
            # this site 403s datacenter IPs: don't knock, use the cache the
            # owner's machine keeps fresh (refresh-local)
            events, detail = [], "refresh: local — skipped in CI"
        else:
            events, detail = scrape_source(s)
        events = [e for e in events if now - timedelta(days=1) <= e.start <= horizon]
        events = trim_frequent(events, now)
        events = [e for e in events if not NOT_PUBLIC.search(e.title)]

        if events:
            cache[s.id] = {"when": now.isoformat(timespec="seconds"),
                           "events": [e.to_dict() for e in events]}
        else:
            stale = cache.get(s.id)
            age_days = _age_days(stale) if stale else 999
            max_age = LOCAL_CACHE_MAX_AGE_DAYS if s.refresh == "local" else CACHE_MAX_AGE_DAYS
            if stale and age_days <= max_age:
                revived = [Event.from_dict(d) for d in stale["events"]]
                events = [e for e in revived if e and now - timedelta(days=1) <= e.start <= horizon]
                for e in events:
                    e.source_id = s.id
                if events:
                    carried += 1
                    detail += f"  [carried forward from {stale['when'][:10]}]"

        print(f"   {detail} → {len(events)} in window")
        all_events.extend(events)
        report.append({"source": s.id, "detail": detail, "events": len(events)})

    _save_cache(cache)
    if carried:
        print(f"   ({carried} source(s) carried forward from cache after an empty scrape)")

    # ── cross-source confirmation + review queue ──────────────────────────
    all_sources = {s.id: s for s in load_sources()}
    merged = group_events(all_events, all_sources)
    decisions = load_decisions()

    published: list[dict] = []
    queue: list[dict] = []
    stats = {"confirmed": 0, "multi_source": 0, "trusted_single": 0,
             "reviewed_in": 0, "reviewed_out": 0, "queued": 0}

    for m in sorted(merged, key=lambda x: x.event.start):
        verdict = classify(m, all_sources)
        d = m.event.to_dict()
        d["sources"] = m.source_links
        d["source_count"] = len(set(m.source_ids))
        d["tabling_reasons"] = classify_tabling(d["title"], d.get("description", ""))
        d["tabling"] = bool(d["tabling_reasons"])

        if verdict == "confirmed":
            d["verification"] = "confirmed"
            stats["confirmed"] += 1
            stats["multi_source" if d["source_count"] >= 2 else "trusted_single"] += 1
            published.append(d)
            continue

        decision = decisions.get(m.key)
        if decision == "approve":
            d["verification"] = "reviewed"
            stats["reviewed_in"] += 1
            published.append(d)
        elif decision == "reject":
            stats["reviewed_out"] += 1
        else:
            stats["queued"] += 1
            queue.append({
                "key": m.key,
                "reason": review_reason(m, all_sources),
                "source_ids": sorted(set(m.source_ids)),
                "sources": m.source_links,
                "title": d["title"],
                "start": d["start"],
                "end": d["end"],
                "all_day": d["all_day"],
                "venue": d["venue"],
                "city": d["city"],
                "area": d["area"],
                "category": d["category"],
                "price": d["price"],
                "description": d["description"][:600],
                "image": d["image"],
                "source": m.source_links[0] if m.source_links else {"name": "", "url": ""},
            })

    published.sort(key=lambda e: e["start"] or "")
    queue.sort(key=lambda q: q["start"] or "")

    generated = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    if args.only:
        # a partial run must not overwrite the published site data
        print(f"\n[--only] {len(all_events)} raw → {len(merged)} merged → "
              f"{len(published)} would publish, {len(queue)} would queue "
              f"(site/data/ NOT written)")
        fetch.shutdown()
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "events.json").write_text(json.dumps({
        "generated": generated,
        "count": len(published),
        "areas": sorted({e["area"] for e in published if e["area"]}),
        "categories": sorted({e["category"] for e in published}),
        "events": published,
    }, indent=2), encoding="utf-8")
    (DATA_DIR / "review_queue.json").write_text(json.dumps({
        "generated": generated,
        "count": len(queue),
        "events": queue,
    }, indent=2), encoding="utf-8")

    # moderation/decisions.csv is owner-written only (admin page via the
    # Worker API); CI never edits it, so the two can't overwrite each other
    # publish a read-only copy of the decisions file so review.html (served from
    # site/) can seed its state from what's already been decided
    from verify import DECISIONS_PATH
    if DECISIONS_PATH.exists():
        (DATA_DIR / "decisions.csv").write_text(
            DECISIONS_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "last_run.json").write_text(json.dumps(
        {"when": generated, "raw": len(all_events), "merged": len(merged),
         "published": len(published), "queued": len(queue),
         "verification": stats, "sources": report}, indent=2), encoding="utf-8")

    # ── per-source history, auto-promotion, admin source list ────────────────
    queued_by_source: dict[str, int] = {}
    for q in queue:
        for sid in q["source_ids"]:
            queued_by_source[sid] = queued_by_source.get(sid, 0) + 1
    src_hist = _update_source_history(report, queued_by_source, generated[:10])
    promoted = promote_sources(src_hist)
    _write_sources_view(src_hist, queued_by_source)

    # ── recurring-event tracking + trend stats (private dashboard feeds) ──────
    hist_stats = recurrence.update_history(published, HISTORY_PATH)
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    RECURRING_PATH.write_text(
        json.dumps(recurrence.build_recurring_view(
            history, recurrence.load_annual_decisions(ANNUAL_DECISIONS_PATH)), indent=2),
        encoding="utf-8")

    category_counts: dict[str, int] = {}
    for e in published:
        category_counts[e["category"]] = category_counts.get(e["category"], 0) + 1
    recurrence.append_stats_snapshot(STATS_HISTORY_PATH, {
        "date": generated[:10],
        "raw": len(all_events), "merged": len(merged),
        "published": len(published), "queued": len(queue),
        "active_sources": len(sources),
        "categories": category_counts,
    })

    print(f"\n{len(all_events)} raw → {len(merged)} merged → "
          f"{len(published)} published ({stats['multi_source']} multi-source, "
          f"{stats['trusted_single']} trusted, {stats['reviewed_in']} admin-approved), "
          f"{len(queue)} in review queue")
    if promoted:
        print(f"promoted potential -> source: {', '.join(promoted)}")
    print(f"recurrence: {hist_stats['series_total']} series tracked "
          f"(+{hist_stats['new_series']} new, +{hist_stats['new_occurrences']} new occurrences)")
    fetch.shutdown()


def cmd_triage(args) -> None:
    sources = load_sources()
    if args.only:
        wanted = set(args.only.split(","))
        sources = [s for s in sources if s.id in wanted]

    hits = 0
    for s in sources:
        print(f"\n=== {s.id}  {s.target}")
        winner = None
        for adapter in GENERIC:
            try:
                res = adapter.scrape(s)
            except Exception as exc:  # noqa: BLE001
                print(f"  !! {adapter.name}: crashed {type(exc).__name__}: {str(exc)[:80]}")
                continue
            flag = "OK " if res.ok else "-- "
            print(f"  {flag}{adapter.name}: {res.detail}")
            if res.ok and winner is None:
                winner = adapter.name
        if winner:
            hits += 1
            if args.write:
                import srcedit  # line-level edit: keeps comments, trust:, refresh:
                srcedit.set_fields(s.id, status="active",
                                   notes=(s.notes + f" [triage: {winner}]").strip())
    print(f"\n{hits}/{len(sources)} sources yielded events via a generic adapter.")
    if args.write:
        print("Registry updated (statuses set to active where a generic adapter worked).")
    fetch.shutdown()


def _update_source_history(report: list[dict], queued: dict[str, int], day: str) -> dict:
    """{source_id: [{date, events, queued}]}, newest last, last 14 runs."""
    try:
        hist = json.loads(SOURCE_HISTORY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        hist = {}
    for r in report:
        runs = [x for x in hist.get(r["source"], []) if x.get("date") != day]
        runs.append({"date": day, "events": r["events"], "queued": queued.get(r["source"], 0),
                     "carried": "carried forward" in r["detail"]})
        hist[r["source"]] = runs[-14:]
    SOURCE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_HISTORY_PATH.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    return hist


def promote_sources(hist: dict) -> list[str]:
    """potential -> source once a site has produced events on PROMOTE_MIN_RUNS of
    its last PROMOTE_WINDOW runs. Only sources with `autopromote: yes` (set when
    added from the admin page); any manual tier change clears it, so an owner's
    explicit choice is never overridden."""
    import srcedit
    promoted = []
    for s in load_sources():
        if s.effective_tier != "potential" or s.autopromote != "yes":
            continue
        runs = [r for r in hist.get(s.id, []) if not r.get("carried")][-PROMOTE_WINDOW:]
        if sum(1 for r in runs if r["events"] > 0) >= PROMOTE_MIN_RUNS:
            note = (s.notes + f" [auto-promoted to source {datetime.now():%Y-%m-%d}]").strip()
            srcedit.set_fields(s.id, tier="source", autopromote="", notes=note)
            promoted.append(s.id)
    return promoted


def _write_sources_view(hist: dict, queued: dict[str, int]) -> None:
    """site/data/sources.json: every registry entry for the admin page."""
    rows = []
    for s in load_sources():
        runs = hist.get(s.id, [])
        rows.append({
            "id": s.id, "name": s.name, "url": s.url, "events_url": s.events_url,
            "category": s.category, "state": s.state, "area": s.area,
            "platform": s.platform, "status": s.status, "tier": s.effective_tier,
            "tier_set": bool(s.tier), "autopromote": s.autopromote == "yes",
            "refresh": s.refresh, "notes": s.notes,
            "last_events": runs[-1]["events"] if runs else None,
            "last_queued": queued.get(s.id, 0),
            "runs": runs[-PROMOTE_WINDOW:],
        })
    SOURCES_VIEW_PATH.write_text(json.dumps({
        "generated": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "promote_rule": {"window": PROMOTE_WINDOW, "min_runs": PROMOTE_MIN_RUNS},
        "sources": rows}, indent=1), encoding="utf-8")


def cmd_refresh_local(args) -> None:
    """Scrape the `refresh: local` sources (sites that 403 GitHub's datacenter
    IPs) from this machine. Like `scrape --only`, it updates
    scraper/output/source_cache.json and never writes site/data/; commit the
    cache and CI carries these events forward (LOCAL_CACHE_MAX_AGE_DAYS)."""
    ids = [s.id for s in load_sources() if s.refresh == "local" and s.status == "active"]
    if not ids:
        print("No sources marked refresh: local.")
        return
    args.only = ",".join(ids)
    cmd_scrape(args)


def cmd_list(args) -> None:
    for s in load_sources():
        print(f"{s.status:9} {s.category:8} {s.state:3} {s.id:28} {s.target}")


def main() -> None:
    p = argparse.ArgumentParser(prog="northland-events scraper")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scrape")
    sp.add_argument("--only", help="comma-separated source ids")
    sp.set_defaults(func=cmd_scrape)

    tp = sub.add_parser("triage")
    tp.add_argument("--only", help="comma-separated source ids")
    tp.add_argument("--write", action="store_true", help="flip working sources to status: active")
    tp.set_defaults(func=cmd_triage)

    rp = sub.add_parser("refresh-local", help="scrape refresh: local sources into the cache")
    rp.set_defaults(func=cmd_refresh_local)

    lp = sub.add_parser("list")
    lp.set_defaults(func=cmd_list)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())

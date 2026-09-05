"""Northland Events scraper — orchestrator.

Usage:
  python main.py triage [--only id,id]      probe every source, report what yields events
  python main.py scrape [--only id,id]      run active sources, write site/data/events.json
  python main.py list                       print the registry

Run from the scraper/ directory.
"""
from __future__ import annotations

import argparse
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
from registry import load_sources, save_sources, Source
from adapters import GENERIC, BY_PLATFORM
from models import Event
from verify import group_events, classify, load_decisions, append_decisions_seen
from tabling import classify_tabling
import recurrence

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "site" / "data"
REPORT_DIR = ROOT / "scraper" / "output"
CACHE_PATH = REPORT_DIR / "source_cache.json"
HISTORY_PATH = DATA_DIR / "event_history.json"
RECURRING_PATH = DATA_DIR / "recurring_events.json"
STATS_HISTORY_PATH = DATA_DIR / "scrape_stats_history.json"

HORIZON_DAYS = 200
CACHE_MAX_AGE_DAYS = 6      # carry a source's last-good events forward this long


def adapters_for(source: Source):
    if source.platform and source.platform in BY_PLATFORM:
        return [BY_PLATFORM[source.platform]]
    return GENERIC


def scrape_source(source: Source) -> tuple[list[Event], str]:
    for adapter in adapters_for(source):
        result = adapter.scrape(source)
        if result.ok and result.events:
            return result.events, f"{adapter.name}: {result.detail}"
    return [], "no adapter produced events"


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
        events, detail = scrape_source(s)
        events = [e for e in events if now - timedelta(days=1) <= e.start <= horizon]

        if events:
            cache[s.id] = {"when": now.isoformat(timespec="seconds"),
                           "events": [e.to_dict() for e in events]}
        else:
            stale = cache.get(s.id)
            age_days = _age_days(stale) if stale else 999
            if stale and age_days <= CACHE_MAX_AGE_DAYS:
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

    append_decisions_seen(queue, decisions)
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

    # ── recurring-event tracking + trend stats (private dashboard feeds) ──────
    hist_stats = recurrence.update_history(published, HISTORY_PATH)
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    RECURRING_PATH.write_text(
        json.dumps(recurrence.build_recurring_view(history), indent=2), encoding="utf-8")

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
            res = adapter.scrape(s)
            flag = "OK " if res.ok else "-- "
            print(f"  {flag}{adapter.name}: {res.detail}")
            if res.ok and winner is None:
                winner = adapter.name
        if winner:
            hits += 1
            if args.write:
                s.status = "active"
                s.notes = (s.notes + f" [triage: {winner}]").strip()
    print(f"\n{hits}/{len(sources)} sources yielded events via a generic adapter.")
    if args.write:
        save_sources(load_sources_merged(sources))
        print("Registry updated (statuses set to active where a generic adapter worked).")
    fetch.shutdown()


def load_sources_merged(updated: list[Source]) -> list[Source]:
    by_id = {s.id: s for s in updated}
    return [by_id.get(s.id, s) for s in load_sources()]


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

    lp = sub.add_parser("list")
    lp.set_defaults(func=cmd_list)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())

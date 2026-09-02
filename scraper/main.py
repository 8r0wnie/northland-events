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

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "site" / "data"
REPORT_DIR = ROOT / "scraper" / "output"

# When the same event shows up from multiple sources, keep the one whose
# category earliest in this list "owns" it (dedicated calendars > aggregators).
SOURCE_PRIORITY = ["tourism", "news", "chamber", "venue", "gov"]

HORIZON_DAYS = 200


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


def dedup(events: list[Event], sources: dict[str, Source]) -> list[Event]:
    def rank(e: Event) -> tuple:
        src = sources.get(e.source_id)
        cat_rank = SOURCE_PRIORITY.index(src.category) if src and src.category in SOURCE_PRIORITY else 99
        richness = sum(bool(x) for x in (e.description, e.image, e.venue, e.price))
        return (cat_rank, -richness)

    best: dict[str, Event] = {}
    for e in sorted(events, key=rank):
        best.setdefault(e.fingerprint, e)
    return list(best.values())


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

    by_id = {s.id: s for s in sources}
    all_events: list[Event] = []
    report = []
    horizon = datetime.now() + timedelta(days=HORIZON_DAYS)

    for s in sources:
        print(f"→ {s.id} ({s.name})")
        events, detail = scrape_source(s)
        events = [e for e in events if e.start <= horizon and e.start >= datetime.now() - timedelta(days=1)]
        print(f"   {detail} → {len(events)} in window")
        all_events.extend(events)
        report.append({"source": s.id, "detail": detail, "events": len(events)})

    deduped = dedup(all_events, by_id)
    deduped.sort(key=lambda e: e.start)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "count": len(deduped),
        "areas": sorted({e.area for e in deduped if e.area}),
        "categories": sorted({e.category for e in deduped}),
        "events": [e.to_dict() for e in deduped],
    }
    (DATA_DIR / "events.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "last_run.json").write_text(json.dumps(
        {"when": payload["generated"], "raw": len(all_events),
         "deduped": len(deduped), "sources": report}, indent=2), encoding="utf-8")

    print(f"\n{len(all_events)} raw → {len(deduped)} after dedup → site/data/events.json")
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

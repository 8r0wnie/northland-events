"""Scan published events for the data-quality bugs we've already met once.

    cd scraper && python quality.py            # counts per problem and source
    python quality.py --show encoded           # list the events behind one problem

Every check here is a bug that reached the live calendar before (CLAUDE.md
"Known issues"). A non-zero count after adding or changing an adapter is a
regression: find the source, fix the adapter, add a test.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

EVENTS = Path(__file__).resolve().parent.parent / "site" / "data" / "events.json"

CHECKS = {
    "encoded": ("URL-encoded text (%20…)", lambda e, d: len(re.findall(r"%[0-9A-Fa-f]{2}", d)) >= 3),
    "css": ("CSS in the description", lambda e, d: bool(re.search(r"\{[^{}]*:[^{}]*;[^{}]*\}", d))),
    "html": ("HTML tags or entities left", lambda e, d: bool(re.search(r"</?[a-z][a-z0-9]*[\s>/]|&[a-z]+;|&#\d+;", d))),
    "midnight": ("timed event at exactly 12:00 AM", lambda e, d: not e.get("all_day") and e["start"][11:16] == "00:00"),
    "series-bar": ("timed event spanning 7+ days", lambda e, d: bool(
        e.get("end") and not e.get("all_day")
        and datetime.fromisoformat(e["end"]) - datetime.fromisoformat(e["start"]) > timedelta(days=6))),
    "no-title": ("empty or one-character title", lambda e, d: len((e.get("title") or "").strip()) < 2),
}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", choices=sorted(CHECKS), help="list the events behind one problem")
    args = ap.parse_args()

    events = json.loads(EVENTS.read_text(encoding="utf-8"))["events"]
    hits: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        desc = e.get("description") or ""
        for key, (_, test) in CHECKS.items():
            if test(e, desc):
                hits[key].append(e)

    if args.show:
        for e in hits[args.show]:
            print(f"{e['source_id']:28} {e['start'][:16]}  {e['title'][:60]}")
        return
    print(f"{len(events)} published events")
    for key, (label, _) in CHECKS.items():
        rows = hits[key]
        top = Counter(e["source_id"] for e in rows).most_common(4)
        print(f"  {key:11} {len(rows):5}  {label}" + (f"  ← {top}" if rows else ""))


if __name__ == "__main__":
    main()

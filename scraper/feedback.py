"""Community feedback: owner-approved corrections, and what the categorizer
learns from them.

People report problems from the public calendar ("Report a problem"); the
owner resolves each report on /admin. The Worker writes every resolution to
`moderation/corrections.csv` (owner-only, like decisions.csv):

    key       <report id>:<field>   (one row per corrected field)
    decision  apply | ignore | reject
    event     the event's fingerprint (title + day + city, as published)
    field     category | when | venue | city | state | title | url | hide
    value     the corrected value (apply) or the reporter's suggestion
    was       the published value when it was reported
    title, date, source, kind, decided_at   context for people and learning

`apply` rows are re-applied at every scrape (`apply_corrections`).

Category learning (`build_learning`): every applied category fix is a labelled
mistake (was -> value), every rejected category report says the category was
right. A rule "<feature>: <from> -> <to>" is learned when at least
LEARN_MIN_SUPPORT different events agree and LEARN_MIN_SHARE of the evidence
for that feature + category points the same way. Features are title words
and the event's source ("source:<id>"). Rules apply to every event, not just
the reported ones; the owner can switch any rule off (category_rules.csv).
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORRECTIONS_PATH = ROOT / "moderation" / "corrections.csv"
RULES_PATH = ROOT / "moderation" / "category_rules.csv"

FIELDS = {"category", "when", "venue", "city", "state", "title", "url", "hide"}
LEARN_MIN_SUPPORT = 3
LEARN_MIN_SHARE = 0.75

_STOP = set("""
the and for with from this that your our are you all its into over under at on in of to by
a an or as is be will can new free open day night week weekend annual event events join
come more about who what when where how live show series session sessions part
""".split())


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh)]


# ── corrections ──────────────────────────────────────────────────────────────
def load_corrections(path: Path = CORRECTIONS_PATH) -> dict[str, list[tuple[str, str]]]:
    """fingerprint -> [(field, value)] for applied fixes, oldest first."""
    rows = [r for r in _read(path) if r.get("decision") == "apply" and r.get("field") in FIELDS]
    rows.sort(key=lambda r: r.get("decided_at", ""))
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in rows:
        if r.get("event"):
            out[r["event"]].append((r["field"], r.get("value", "")))
    return dict(out)


def _parse_when(value: str):
    """'2026-10-14T10:00/2026-10-14T11:00', '2026-10-14T10:00', '2026-10-14'
    or '2026-10-14/2026-10-16' -> (start, end, all_day); None if unreadable."""
    parts = [p.strip() for p in value.split("/")] if value else []
    if not parts or not parts[0]:
        return None
    try:
        start = datetime.fromisoformat(parts[0])
        end = datetime.fromisoformat(parts[1]) if len(parts) > 1 and parts[1] else None
    except ValueError:
        return None
    all_day = "T" not in parts[0]
    if all_day and end:
        end = datetime.combine(end.date(), time(23, 59, 59))
    if end and end <= start:
        end = None
    return start, end, all_day


def apply_corrections(d: dict, fixes: list[tuple[str, str]]) -> bool:
    """Apply owner-approved fixes to a published event dict in place.
    Returns False when the event should be hidden. The fingerprint is kept,
    so later reports and decisions still find the event."""
    for field, value in fixes:
        if field == "hide":
            return False
        if field == "when":
            parsed = _parse_when(value)
            if parsed:
                start, end, all_day = parsed
                d["start"] = start.isoformat()
                d["end"] = end.isoformat() if end else None
                d["all_day"] = all_day
        elif field in ("category", "venue", "city", "state", "title", "url") and value:
            d[field] = value
        d["corrected"] = True
    return True


# ── category learning ────────────────────────────────────────────────────────
def features(title: str, source_id: str = "") -> set[str]:
    words = {w.strip("'&-") for w in re.findall(r"[a-z][a-z'&-]{2,}", (title or "").lower())}
    out = {w for w in words if len(w) >= 3 and w not in _STOP}
    if source_id:
        out.add(f"source:{source_id}")
    return out


def rule_key(feature: str, src: str, dst: str) -> str:
    return f"{feature}|{src}|{dst}"


def build_learning(corrections_path: Path = CORRECTIONS_PATH,
                   rules_path: Path = RULES_PATH) -> dict:
    """Learn category rules from resolved category reports."""
    rows = [r for r in _read(corrections_path) if r.get("field") == "category"]
    rows.sort(key=lambda r: r.get("decided_at", ""))
    off = {r["key"] for r in _read(rules_path) if r.get("decision") == "off" and r.get("key")}

    # one verdict per event (several people may report the same event)
    mistakes: dict[str, dict] = {}       # event -> {was, to, feats, title}
    confirmed: dict[str, dict] = {}      # event -> {cat, feats}
    for r in rows:
        ev, was, val = r.get("event", ""), r.get("was", ""), r.get("value", "")
        if not ev or not was:
            continue
        feats = features(r.get("title", ""), r.get("source", ""))
        if r.get("decision") == "apply" and val and val != was:
            mistakes[ev] = {"was": was, "to": val, "feats": feats, "title": r.get("title", "")}
            confirmed.pop(ev, None)
        elif r.get("decision") == "reject":
            confirmed[ev] = {"cat": was, "feats": feats}
            mistakes.pop(ev, None)

    support: dict[tuple, list[str]] = defaultdict(list)    # (feat, from, to) -> titles
    evidence: Counter = Counter()                           # (feat, from) -> all verdicts
    for m in mistakes.values():
        for f in m["feats"]:
            support[(f, m["was"], m["to"])].append(m["title"])
            evidence[(f, m["was"])] += 1
    for c in confirmed.values():
        for f in c["feats"]:
            evidence[(f, c["cat"])] += 1

    rules, pending = [], []
    for (f, src, dst), titles in support.items():
        n, total = len(titles), evidence[(f, src)]
        row = {"key": rule_key(f, src, dst), "feature": f, "from": src, "to": dst,
               "support": n, "against": total - n, "examples": sorted(set(titles))[:5]}
        if n >= LEARN_MIN_SUPPORT and n / total >= LEARN_MIN_SHARE:
            row["enabled"] = row["key"] not in off
            row["applied"] = 0
            rules.append(row)
        else:
            pending.append(row)
    rules.sort(key=lambda r: (-r["support"], r["key"]))
    pending.sort(key=lambda r: (-r["support"], r["key"]))

    confusion = Counter((m["was"], m["to"]) for m in mistakes.values())
    by_source = Counter(f.split(":", 1)[1] for m in mistakes.values()
                        for f in m["feats"] if f.startswith("source:"))
    return {
        "min_support": LEARN_MIN_SUPPORT, "min_share": LEARN_MIN_SHARE,
        "corrections": len(mistakes), "confirmations": len(confirmed),
        "rules": rules, "pending": pending[:40],
        "confusion": [{"from": a, "to": b, "count": n} for (a, b), n in confusion.most_common()],
        "by_source": [{"source": s, "count": n} for s, n in by_source.most_common(15)],
    }


def apply_learned(d: dict, rules: list[dict]) -> str:
    """Re-categorize one event dict with the strongest matching enabled rule.
    Returns the rule key used, or ""."""
    feats = features(d.get("title", ""), d.get("source_id", ""))
    best = None
    for r in rules:
        if r.get("enabled") and r["from"] == d.get("category") and r["feature"] in feats:
            if best is None or r["support"] > best["support"]:
                best = r
    if not best:
        return ""
    d["category"] = best["to"]
    best["applied"] = best.get("applied", 0) + 1
    return best["key"]

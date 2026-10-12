"""Community reports: owner-applied corrections and category learning.

    cd scraper && python tests/test_feedback.py
"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedback  # noqa: E402

COLS = ["key", "decision", "event", "field", "value", "was", "title", "date", "source", "kind", "decided_at"]


def _csv(rows: list[dict], cols=COLS) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    fh.close()
    return Path(fh.name)


def _fix(n, event, title, was, to, decision="apply", source="src-a"):
    return {"key": f"r{n}:category", "decision": decision, "event": event, "field": "category",
            "value": to, "was": was, "title": title, "source": source, "kind": "category",
            "decided_at": f"2026-10-12T10:00:{n:02d}Z"}


def test_apply_corrections():
    path = _csv([
        {"key": "a:when", "decision": "apply", "event": "e1", "field": "when",
         "value": "2026-10-14T10:00/2026-10-14T11:00", "decided_at": "1"},
        {"key": "a:category", "decision": "apply", "event": "e1", "field": "category", "value": "community", "decided_at": "2"},
        {"key": "b:city", "decision": "ignore", "event": "e1", "field": "city", "value": "Nowhere", "decided_at": "3"},
        {"key": "c:hide", "decision": "apply", "event": "e2", "field": "hide", "value": "cancelled", "decided_at": "4"},
        {"key": "d:when", "decision": "apply", "event": "e3", "field": "when", "value": "2026-11-01", "decided_at": "5"},
    ])
    fixes = feedback.load_corrections(path)
    d = {"start": "2026-10-14T00:00:00", "end": "2026-10-21T23:59:59", "all_day": False,
         "category": "sports-rec", "city": "Bayfield"}
    assert feedback.apply_corrections(d, fixes["e1"])
    assert (d["start"], d["end"], d["category"], d["city"]) == (
        "2026-10-14T10:00:00", "2026-10-14T11:00:00", "community", "Bayfield"), d   # ignored row not applied
    assert d["corrected"] is True
    assert feedback.apply_corrections({}, fixes["e2"]) is False                     # hidden
    d3 = {"start": "2026-10-30T19:00:00", "end": None, "all_day": False}
    feedback.apply_corrections(d3, fixes["e3"])
    assert d3["start"] == "2026-11-01T00:00:00" and d3["all_day"] is True, d3


def test_learns_after_three_agreeing_events():
    rows = [_fix(1, "e1", "Community Bingo Night", "family", "community"),
            _fix(2, "e2", "Bingo at the Legion", "family", "community"),
            # the same event reported twice counts once
            _fix(3, "e2", "Bingo at the Legion", "family", "community")]
    L = feedback.build_learning(_csv(rows), _csv([], ["key", "decision", "decided_at"]))
    assert not L["rules"], L["rules"]
    assert any(p["feature"] == "bingo" and p["support"] == 2 for p in L["pending"]), L["pending"]

    rows.append(_fix(4, "e3", "Bingo Fundraiser", "family", "community"))
    L = feedback.build_learning(_csv(rows), _csv([], ["key", "decision", "decided_at"]))
    bingo = [r for r in L["rules"] if r["feature"] == "bingo"]
    assert bingo and bingo[0]["support"] == 3 and bingo[0]["enabled"], L["rules"]
    assert L["confusion"][0] == {"from": "family", "to": "community", "count": 3}, L["confusion"]

    d = {"title": "Monday Bingo", "category": "family", "source_id": "other-src"}
    assert feedback.apply_learned(d, L["rules"]) and d["category"] == "community", d
    d = {"title": "Monday Bingo", "category": "music", "source_id": "x"}      # only from the learned category
    assert not feedback.apply_learned(d, L["rules"]) and d["category"] == "music"


def test_confirmations_block_a_rule_and_owner_can_switch_off():
    rows = [_fix(i, f"e{i}", f"Bingo {i}", "family", "community") for i in range(1, 4)]
    # two rejected reports: "bingo" events that really were family
    rows += [_fix(10 + i, f"k{i}", f"Family Bingo {i}", "family", "community", decision="reject")
             for i in range(2)]
    L = feedback.build_learning(_csv(rows), _csv([], ["key", "decision", "decided_at"]))
    assert not [r for r in L["rules"] if r["feature"] == "bingo"], L["rules"]     # 3/5 < 75%

    rows = [_fix(i, f"e{i}", f"Bingo {i}", "family", "community") for i in range(1, 4)]
    off = _csv([{"key": "bingo|family|community", "decision": "off"}], ["key", "decision", "decided_at"])
    L = feedback.build_learning(_csv(rows), off)
    rule = [r for r in L["rules"] if r["feature"] == "bingo"][0]
    assert rule["enabled"] is False
    d = {"title": "Bingo", "category": "family"}
    assert not feedback.apply_learned(d, L["rules"]) and d["category"] == "family"


def test_source_feature():
    rows = [_fix(i, f"e{i}", t, "other", "education", source="arrowhead-library-system")
            for i, t in enumerate(["Tech Help", "Resume Workshop", "Genealogy Club"], 1)]
    L = feedback.build_learning(_csv(rows), _csv([], ["key", "decision", "decided_at"]))
    assert any(r["feature"] == "source:arrowhead-library-system" for r in L["rules"]), L["rules"]
    assert L["by_source"][0] == {"source": "arrowhead-library-system", "count": 3}


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failed else 0)

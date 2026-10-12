"""Source tiers (trusted / source / potential) and owner decisions.

    cd scraper && python tests/test_tiers.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Event  # noqa: E402
from registry import Source  # noqa: E402
from verify import group_events, classify, review_reason  # noqa: E402
import recurrence  # noqa: E402


def src(sid, tier="", category="venue", url=None, trust=""):
    return Source(id=sid, name=sid, url=url or f"https://{sid}.example", events_url="",
                  category=category, state="MN", area="Duluth", platform="x",
                  status="active", tier=tier, trust=trust)


def ev(sid, inferred=False):
    return Event(source_id=sid, title="Harvest Fest", start=datetime(2026, 10, 24, 10),
                 city="Duluth", inferred=inferred)


def verdict(events, sources):
    by_id = {s.id: s for s in sources}
    (m,) = group_events(events, by_id)
    return classify(m, by_id), review_reason(m, by_id)


def test_one_source_nightly_run_stays_separate():
    # zeitgeist / umd-bulldogs 2026-10-12: consecutive nights from one source
    # were merged by the +/-1 day slack (Fri + Sat hockey became one game)
    by_id = {s.id: s for s in [src("a"), src("b")]}
    shows = [Event(source_id="a", title="Urinetown", start=datetime(2026, 11, d, 19, 30), city="Duluth")
             for d in (12, 13, 14)]
    shows.append(Event(source_id="a", title="Urinetown", start=datetime(2026, 11, 15, 14), city="Duluth"))
    assert len(group_events(shows, by_id)) == 4
    # an exact duplicate from the same source still merges
    dup = [Event(source_id="a", title="Urinetown", start=datetime(2026, 11, 12, 19, 30), city="Duluth")] * 2
    assert len(group_events(dup, by_id)) == 1
    # a second website a day off still corroborates
    pair = [Event(source_id="a", title="Harvest Fest", start=datetime(2026, 10, 24, 10), city="Duluth"),
            Event(source_id="b", title="Harvest Fest", start=datetime(2026, 10, 25, 10), city="Duluth")]
    (m,) = group_events(pair, by_id)
    assert sorted(m.source_ids) == ["a", "b"]


def test_effective_tier_defaults():
    assert src("a").effective_tier == "trusted"
    assert src("b", category="social").effective_tier == "source"
    assert src("c", trust="low").effective_tier == "source"
    assert src("d", tier="potential", category="gov").effective_tier == "potential"


def test_trusted_alone_publishes():
    assert verdict([ev("a")], [src("a", "trusted")])[0] == "confirmed"


def test_trusted_inferred_needs_review():
    assert verdict([ev("a", inferred=True)], [src("a", "trusted")]) == \
        ("needs-review", "read from page text")


def test_two_sources_corroborate():
    assert verdict([ev("a"), ev("b")], [src("a", "source"), src("b", "source")])[0] == "confirmed"


def test_one_source_waits():
    assert verdict([ev("a")], [src("a", "source")]) == \
        ("needs-review", "single source, not yet corroborated")


def test_same_site_is_one_witness():
    s1 = src("a", "source", url="https://hurleywi.com/fair")
    s2 = src("b", "source", url="https://www.hurleywi.com/fest")
    assert verdict([ev("a"), ev("b")], [s1, s2])[0] == "needs-review"


def test_potential_never_witnesses():
    assert verdict([ev("a"), ev("p")], [src("a", "source"), src("p", "potential")])[0] == "needs-review"
    assert verdict([ev("p")], [src("p", "potential")]) == ("needs-review", "potential source")
    # but a trusted source still confirms an event a potential source also lists
    assert verdict([ev("t"), ev("p")], [src("t", "trusted"), src("p", "potential")])[0] == "confirmed"


def test_annual_decisions():
    hist = {"series": {"harvest fest|duluth": {
        "title": "3rd Annual Harvest Fest", "area": "Duluth",
        "occurrences": {"2026": {"date": "2026-10-24", "url": "u"}}}}}
    assert len(recurrence.build_recurring_view(hist)["likely"]) == 1
    v = recurrence.build_recurring_view(hist, {"harvest fest|duluth": "confirm"})
    assert len(v["confirmed"]) == 1 and v["confirmed"][0]["owner_decision"] == "confirm"
    v = recurrence.build_recurring_view(hist, {"harvest fest|duluth": "reject"})
    assert not v["likely"] and not v["confirmed"] and len(v["rejected"]) == 1


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

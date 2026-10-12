"""Recurring series must become dated occurrences, never one long block.
Real Simpleview (bayfield-chamber) docs from 2026-10-12.

    cd scraper && python tests/test_series.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Event  # noqa: E402
from adapters.simpleview import events_from_doc, expand_recurrence, parse_time_range  # noqa: E402

TODAY = date(2026, 10, 12)
BASE = "https://www.bayfield.org"
CHAIR_YOGA = {"_id": "a", "title": "Chair Yoga", "location": "Bayfield Presbyterian Church",
              "startDate": "2026-10-07T05:00:00.000Z", "endDate": "2026-10-22T04:59:59.000Z",
              "recurrence": "Recurring weekly on Wednesday", "recurType": 3,
              "url": "/event/chair-yoga/5895/"}
CHAIR_YOGA_PAGE = {"time": "10:00 AM to 11:00 AM", "admission": "$10 suggested donation",
                   "recurrence": "Recurring weekly on Wednesday",
                   "description": "<p>Chair Yoga for increased strength. Led by Tracy Chipman.</p>"}


def test_chair_yoga_is_two_one_hour_classes():
    evs = events_from_doc(CHAIR_YOGA, BASE, set(), CHAIR_YOGA_PAGE, today=TODAY)
    assert [(e.start, e.end) for e in evs] == [
        (datetime(2026, 10, 14, 10), datetime(2026, 10, 14, 11)),
        (datetime(2026, 10, 21, 10), datetime(2026, 10, 21, 11))], evs
    assert evs[0].price == "$10 suggested donation" and not evs[0].all_day
    assert "Tracy Chipman" in evs[0].description


def test_custom_dates_use_listed_occurrences():
    # recurType 99: the span is Oct 14-21 but the site lists only Oct 14
    doc = dict(CHAIR_YOGA, _id="b", startDate="2026-10-14T05:00:00.000Z", recurType=99)
    doc.pop("recurrence")
    evs = events_from_doc(doc, BASE, {date(2026, 10, 14)}, {"time": "10:00 AM to 11:00 AM"}, today=TODAY)
    assert [e.start for e in evs] == [datetime(2026, 10, 14, 10)], evs


def test_no_time_means_all_day_not_midnight():
    doc = {"_id": "c", "title": "Open Mic", "startDate": "2026-10-21T05:00:00.000Z",
           "recurrence": "Recurring monthly on the 3rd Wednesday", "url": "/event/open-mic/5979/"}
    evs = events_from_doc(doc, BASE, set(), {}, today=TODAY)
    assert evs[0].all_day and evs[0].start == datetime(2026, 10, 21), evs
    assert [e.start.date() for e in evs] == [date(2026, 10, 21), date(2026, 11, 18)], evs


def test_daily_exhibit_is_one_span():
    doc = {"_id": "d", "title": "CBAC Members Only Art October Show",
           "startDate": "2026-10-01T05:00:00.000Z", "endDate": "2026-10-31T04:59:59.000Z",
           "recurrence": "Recurring daily", "url": "/event/x/1/"}
    evs = events_from_doc(doc, BASE, set(), {}, today=TODAY)
    assert len(evs) == 1 and evs[0].all_day, evs
    assert evs[0].end == datetime(2026, 10, 30, 23, 59, 59), evs


def test_recurrence_patterns():
    lo, hi = TODAY, date(2026, 12, 11)
    every4 = expand_recurrence("Recurring every 4 weeks on Thursday", date(2026, 3, 5), None, lo, hi)
    assert every4 == [date(2026, 10, 15), date(2026, 11, 12), date(2026, 12, 10)], every4
    multi = expand_recurrence("Recurring weekly on Tuesday, Saturday", date(2026, 9, 16),
                              date(2026, 10, 17), lo, hi)
    assert multi == [date(2026, 10, 13), date(2026, 10, 17)], multi
    assert expand_recurrence("Recurring on a whim", date(2026, 9, 1), None, lo, hi) is None


def test_time_ranges():
    assert parse_time_range("10:00 AM to 11:00 AM") == (time(10), time(11))
    assert parse_time_range("7 p.m.") == (time(19), None)
    assert parse_time_range("") == (None, None)


def test_normalize_backstop_any_adapter():
    # any adapter: a timed weekly series published as first-class -> last-class
    e = Event(source_id="x", title="Chair Yoga", start=datetime(2026, 10, 14, 10),
              end=datetime(2026, 10, 21, 11), description="Recurring weekly on Wednesday").normalize()
    assert e.end is None, e.end
    # a real multi-week run with no repeat wording keeps its span
    e = Event(source_id="x", title="Bentleyville Tour of Lights", start=datetime(2026, 11, 21, 17),
              end=datetime(2026, 12, 26, 21), description="Free walk-through light show.").normalize()
    assert e.end == datetime(2026, 12, 26, 21), e.end


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

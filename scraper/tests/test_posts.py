"""Regression cases for the `posts` text-inference adapter (and ics helpers).

Each case came from a real site during triage (2026-10-11). Run before
changing adapters/posts.py:

    cd scraper && python -m pytest tests -q        # or: python tests/test_posts.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.posts import find_dates, find_time, _title_from_line, _title_ok  # noqa: E402
from adapters.ics import _gcal_id  # noqa: E402

REF = date(2026, 10, 11)  # a Sunday


def titles(text: str, ref: date = REF) -> list[tuple[str, str]]:
    out, prev = [], 0
    for start, _end, s, e in find_dates(text, ref):
        out.append((start.isoformat(), _title_from_line(text, s, e, prev)))
        prev = e
    return out


def test_title_after_separator():
    assert titles("Oct. 25 – Halloween Parade, 5 pm, Main St") == [("2026-10-25", "Halloween Parade")]
    assert titles("Oct 24: 10am-2pm Harvest Fest (City Park)") == [("2026-10-24", "Harvest Fest")]


def test_title_before_date_listing():
    # GovOffice/listing style: title, then its date (and time)
    t = ("Monthly Board Game Night @ Sandstone Library Thursday, October 29, 2026 at 6:00 PM "
         "Climbers Fest Friday, October 16, 2026")
    assert titles(t) == [("2026-10-29", "Monthly Board Game Night @ Sandstone Library"),
                         ("2026-10-16", "Climbers Fest")]


def test_weak_lead_in_and_trailing_verbs():
    assert titles("Join us Saturday, October 17 for the Harvest Fest!") == [("2026-10-17", "Harvest Fest")]
    assert titles("The Fall Fish Fry is on Friday, Oct 23. Call Kathy at 320-245-5241") == [
        ("2026-10-23", "Fall Fish Fry")]


def test_day_first_calendar_grid():
    # Drupal / CivicPlus HCMS grid text (Washburn WI)
    t = ("4:30p Personnel & Finance Committee Mon, 12 Oct 2026 4:30pm 2p Yard Waste Site "
         "Wed, 14 Oct 2026 2pm 4p Bayfield St. Beautification Thu, 15 Oct 2026 4pm")
    assert titles(t) == [("2026-10-12", "Personnel & Finance Committee"),
                         ("2026-10-14", "Yard Waste Site"),
                         ("2026-10-15", "Bayfield St. Beautification")]


def test_weekday_must_match():
    # Oct 18 2026 is a Sunday, and in 2027 a Monday: reject both
    assert find_dates("Join us Saturday, October 18 for the Harvest Fest!", REF) == []


def test_publication_and_deadline_dates_ignored():
    for t in ("Posted on October 2, 2026", "Applications due by Nov 1", "Deadline: December 5"):
        assert find_dates(t, REF) == [], t


def test_yearless_far_future_is_a_past_mention():
    # Hoops Brewing's "The final pour. February 23rd." read in October
    assert find_dates("The final pour. February 23rd.", REF) == []
    assert [d[0].isoformat() for d in find_dates("Holiday bazaar December 5", REF)] == ["2026-12-05"]


def test_ranges():
    (start, end, *_), = find_dates("Dec 31 - Jan 2 winter fest", REF)
    assert (start.isoformat(), end.isoformat()) == ("2026-12-31", "2027-01-02")


def test_times():
    assert find_time("Show at 7:30 p.m.") == (19, 30)
    assert find_time("Harvest Fest Oct 24 10a-2p") == (10, 0)
    assert find_time("apple pie at 3 a day") is None
    # ranges borrow the end's meridiem; noon is a time (KAXE roundups)
    assert find_time("giving event - 4:30-6:30 p.m. at Timberlake") == (16, 30)
    assert find_time("program - 10-11 a.m. at the Eagles Club") == (10, 0)
    assert find_time("presentation - noon-1 p.m. at Chucker Auditorium") == (12, 0)
    assert find_time("brunch 11-1 p.m.") == (11, 0)


def test_title_filters():
    for bad in ("6:00 pm - 11:00 pm", "Oct 24 | 7pm", "Events in October 2026", "Begins",
                "What’s Tappening – September", "For the most up-to-date event information follow us on social",
                "Help with the application process is available in person at"):
        assert not _title_ok(bad), bad
    for good in ("Trivia Mafia", "Bingo!", "Live Music: Little Way", "Breakfast with the Clauses"):
        assert _title_ok(good), good


def test_roundup_day_headings():
    # date-only heading over undated "Title - time at Venue" items
    from datetime import timedelta
    from registry import Source
    from adapters.posts import _from_page
    d = REF + timedelta(days=4)   # Thursday, Oct. 15
    html = (f"<h3>{d:%A}, {d:%b}. {d.day}</h3>"
            "<p>Blithe Spirit - 7 p.m. at the High School Auditorium</p>"
            "<p>Chili cook-off - 11 a.m.-1 p.m. at Northern Pacific Center</p>")
    src = Source(id="t", name="t", url="https://x", events_url="", category="news",
                 state="MN", area="X", platform="posts", status="active")
    evs = _from_page(src, html, "https://x", REF)
    assert [(e.title, e.start.hour) for e in evs] == [("Blithe Spirit", 19), ("Chili cook-off", 11)]


def test_time_zones():
    from models import parse_dt, from_epoch
    # UTC instants become Central wall time (ChamberMaster "…Z" microdata)
    assert str(parse_dt("2026-12-09T13:45:00Z")) == "2026-12-09 07:45:00"
    # local-offset and naive values are left as published
    assert str(parse_dt("2026-07-10T19:00:00-05:00")) == "2026-07-10 19:00:00"
    assert str(parse_dt("2026-10-12 19:00")) == "2026-10-12 19:00:00"
    # epoch → Central regardless of the machine's zone (CI runs in UTC)
    assert str(from_epoch(1791849600)) == "2026-10-12 19:00:00"


def test_not_public_filter():
    import main
    for t in ("NWLS Closed", "Horizon Circulation Training for Library Staff",
              "Library closed for Thanksgiving", "Staff Meeting"):
        assert main.NOT_PUBLIC.search(t), t
    for t in ("Closed Captioned Movie Night", "Fall Open House", "Garage Sale"):
        assert not main.NOT_PUBLIC.search(t), t


def test_gcal_ids():
    assert _gcal_id("ely.mn.us_e8e3c2380hhmm5kgbkcv9ga7qs%40group.calendar.google.com") == \
        "ely.mn.us_e8e3c2380hhmm5kgbkcv9ga7qs%40group.calendar.google.com"
    # base64-encoded src (Grand Marais embed)
    assert _gcal_id("Z3JhbmRtYXJhaXMuY2l0eV9hYmNAZ3JvdXAuY2FsZW5kYXIuZ29vZ2xlLmNvbQ") == \
        "grandmarais.city_abc%40group.calendar.google.com"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failed else 0)

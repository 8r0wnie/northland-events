"""Description cleanup (models.clean_description) - real cases from 2026-10-12.

    cd scraper && python tests/test_descriptions.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import clean_description as clean, truncate_desc  # noqa: E402

REIF = ("SPONSORED BY .fusion-accordian #accordion--1 .panel-title a .fa-fusion-box{ color: #ffffff;}"
        ".fusion-accordian #accordion--1 .panel-title a .fa-fusion-box:before{ font-size: 13px; width: 13px;}"
        ".fusion-accordian #accordion--1 .panel-title a:hover, #accordion--1 .fusion-toggle-boxed-mode:hover "
        ".panel-title a { color: #f05023;}.fusion-accordian #accordion--1 .panel-title .active .fa-fusion-box"
        "{ background-color: #f05023!important;border-color: #f05023!important;}Wilson Fairchild Wilson Fairchild "
        "is more than just a country music duo. Concertgoers enjoy timeless hits alongside new material that "
        "honors the spirit of The Statler Brothers w")


def test_reif_css_sponsor_and_title_copy():
    out = clean(REIF, "Wilson Fairchild")
    assert out.startswith("Wilson Fairchild is more than"), out
    assert "{" not in out and "fusion" not in out and "SPONSORED" not in out, out
    assert out.endswith("…"), out      # source cut "Brothers w" mid-word


def test_url_encoded_revize_desc():
    raw = ("The%20RBC%20Program%2FDevelopment%20meeting%20is%20held%20in%20the%20FDL%20Tribal%20Center"
           "%20Chambers.%20Click%20the%26nbsp%3B%3Ca%20href%3D%22https%3A%2F%2Fx%22%3EZoom%20Link%3C%2Fa%3E")
    out = clean(raw, "RBC Meeting Notice")
    assert out.startswith("The RBC Program/Development meeting is held"), out
    assert "%" not in out and "<a" not in out and "Zoom Link" in out, out
    # ordinary text with percent signs is left alone
    assert clean("Save 50% on tickets, 100% fun for kids and families all day", "") == \
        "Save 50% on tickets, 100% fun for kids and families all day"


def test_html_and_entities():
    assert clean('Council Chambers<br> <br>Meeting Link:<br><a href="x">Pine City MN - YouTube</a>', "Council") == \
        "Council Chambers Meeting Link: Pine City MN - YouTube"
    assert clean('<p dir="ltr"><span>Free writing workshops&nbsp;at libraries.</span></p>', "W") == \
        "Free writing workshops at libraries."
    assert clean("<style>.a{color:red}</style><script>var x=1;</script>Real text here.", "T") == "Real text here."


def test_boilerplate_labels_only():
    assert clean("Presented by Mega Events TICKETS Tickets on sale now! ABOUT THE SHOW Great show. "
                 "KNOW BEFORE YOU GO", "Rock Orchestra") == "Presented by Mega Events Tickets on sale now! Great show."
    assert clean("Revival Nights 2026 TICKETS Tickets on sale... Read More", "x") == "Revival Nights 2026 Tickets on sale..."
    assert clean("Great show tonight. Read more »", "x") == "Great show tonight."
    # ordinary sentences are left alone
    s = "You can buy tickets at the door. Read more about the band on their site."
    assert clean(s, "Show") == s
    s = "This program is sponsored by the League of Women Voters."
    assert clean(s, "Forum") == s


def test_truncation_at_sentence_or_word():
    long = "First sentence here. " * 80
    out = truncate_desc(long, 200)
    assert len(out) <= 200 and out.endswith("."), out
    words = "word " * 100
    out = truncate_desc(words, 50)
    assert out.endswith("…") and not out.endswith(" …"), out


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

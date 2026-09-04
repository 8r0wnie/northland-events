"""Heuristic flag: is this a good tabling opportunity for PBS North?

Scope was set directly by PBS North: fairs/festivals/community celebrations,
expos/health fairs/community resource days, and parades/large outdoor public
gatherings. Farmers markets are explicitly excluded — recurring, small-footprint,
not worth a standing table.

This only tags events; it never filters the public calendar. The tag rides
along in site/data/events.json and is read by the private, passphrase-gated
site/tabling.html view.

Matching is against the **title only**, not the description. Descriptions
routinely mention "festival" in passing — a venue's proper name ("Bayfront
Festival Park", "Barker's Island Festival Pavilion"), an artist's tour bio, a
workshop about producing festival stages — none of which mean the event
itself is one. An event that actually is a fair, festival, expo, or parade
says so in its own title; that's a much cleaner signal for a list a human
will act on by literally showing up with a table.
"""
from __future__ import annotations

import re

_EXCLUDE = re.compile(
    r"farmers?.?s?\s*market"
    # Fairgrounds sources bring their own governance calendars along with them
    # (e.g. "Fair Board Meeting-September") — an administrative meeting *about*
    # a fair/parade is never itself a place to set up a table.
    r"|\bmeeting\b",
    re.I)

# "fest"/"festival" is matched as a bare substring (not \bfest\b) because it
# routinely appears fused into a compound name with no word boundary in
# between: Oktoberfest, Oktoberfestival, MuralFest. manifest/infest are the
# only common English words that would false-positive on that substring.
_FEST_SUBSTRING = re.compile(r"fest", re.I)
_FEST_FALSE_POSITIVE = re.compile(r"manifest|infest", re.I)

_FAIR_ETC = re.compile(r"\b(fair|jubilee|community\s+days?|founders'?\s+day)\b", re.I)

_EXPO = re.compile(
    r"\b(expo|health\s+fair|resource\s+(day|fair)|job\s+fair|"
    r"community\s+(resource|info(rmation)?)\s+day)\b", re.I)

# A "Black Parade" tribute show, or any concert billed around an album/song of
# that name, isn't a street parade — exclude the parade match when the title
# reads as a concert.
_PARADE = re.compile(r"\b(parade|block\s+party)\b", re.I)
_EXCLUDE_PARADE = re.compile(r"\btribute\b", re.I)


def _is_fair_or_festival(title: str) -> bool:
    if _FAIR_ETC.search(title):
        return True
    return bool(_FEST_SUBSTRING.search(title)) and not _FEST_FALSE_POSITIVE.search(title)


def classify_tabling(title: str, description: str = "") -> list[str]:
    """Return matched reason tags (possibly several); [] if not a fit."""
    title = title or ""
    if _EXCLUDE.search(title):
        return []
    reasons = []
    if _is_fair_or_festival(title):
        reasons.append("Fair / festival")
    if _EXPO.search(title):
        reasons.append("Expo / health fair / resource day")
    if _PARADE.search(title) and not _EXCLUDE_PARADE.search(title):
        reasons.append("Parade / large public gathering")
    return reasons

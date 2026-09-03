"""Adapter registry.

Each adapter takes a Source and yields Events. The orchestrator uses a
platform-specific adapter when the registry pins one (`platform:` in
sources.yaml), otherwise it walks GENERIC in order and takes the first adapter
that returns events.

GENERIC order matters: cheap/precise first, expensive/broad last.
"""
from __future__ import annotations

from .base import Adapter, AdapterResult
from .tribe import TribeEventsAdapter
from .weblink import WebLinkAdapter
from .civicplus import CivicPlusAdapter
from .revize import RevizeAdapter
from .jsonld import JsonLdAdapter
from .ics import IcsAdapter
from .chamberorganizer import ChamberOrganizerAdapter
from .chambermaster import ChamberMasterAdapter

GENERIC: list[Adapter] = [
    TribeEventsAdapter(),      # one API call, unambiguous when present
    WebLinkAdapter(),          # one API call once the tenant is known
    CivicPlusAdapter(),        # one RSS feed fetch
    RevizeAdapter(),           # one JSON handler fetch
    JsonLdAdapter(),           # page fetch (+ optional render)
    IcsAdapter(),              # page fetch + feed fetch
    ChamberOrganizerAdapter(), # ~6 month POSTs
    ChamberMasterAdapter(),    # many calendar + detail-page fetches
]

# Platform id (from sources.yaml) -> adapter to use directly, skipping the chain.
BY_PLATFORM: dict[str, Adapter] = {
    "wordpress-tribe": TribeEventsAdapter(),
    "tribe": TribeEventsAdapter(),
    "weblink": WebLinkAdapter(),
    "civicplus": CivicPlusAdapter(),
    "revize": RevizeAdapter(),
    "chamberorganizer": ChamberOrganizerAdapter(),
    "chambermaster": ChamberMasterAdapter(),
    "growthzone": ChamberMasterAdapter(),
}

__all__ = ["Adapter", "AdapterResult", "GENERIC", "BY_PLATFORM"]

"""Adapter registry.

Each adapter takes a Source and yields Events. The orchestrator uses a
platform-specific adapter when the registry pins one, otherwise it walks
GENERIC in order and takes the first adapter that returns events.

GENERIC order matters: cheap/precise first, expensive/broad last.
  tribe             one API call, unambiguous when present
  weblink           one API call once the tenant is known
  jsonld            one page fetch (+ optional render)
  ics               one page fetch + a feed fetch
  chamberorganizer  ~6 month POSTs to a public API
  chambermaster     several calendar pages + many detail pages
"""
from __future__ import annotations

from .base import Adapter, AdapterResult
from .tribe import TribeEventsAdapter
from .weblink import WebLinkAdapter
from .jsonld import JsonLdAdapter
from .ics import IcsAdapter
from .chamberorganizer import ChamberOrganizerAdapter
from .chambermaster import ChamberMasterAdapter

GENERIC: list[Adapter] = [
    TribeEventsAdapter(),
    WebLinkAdapter(),
    JsonLdAdapter(),
    IcsAdapter(),
    ChamberOrganizerAdapter(),
    ChamberMasterAdapter(),
]

# Platform id (from sources.yaml) -> adapter to use directly, skipping the chain.
BY_PLATFORM: dict[str, Adapter] = {
    "wordpress-tribe": TribeEventsAdapter(),
    "tribe": TribeEventsAdapter(),
    "weblink": WebLinkAdapter(),
    "chamberorganizer": ChamberOrganizerAdapter(),
    "chambermaster": ChamberMasterAdapter(),
    "growthzone": ChamberMasterAdapter(),
}

__all__ = ["Adapter", "AdapterResult", "GENERIC", "BY_PLATFORM"]

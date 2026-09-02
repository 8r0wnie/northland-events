"""Base adapter contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol

from models import Event
from registry import Source


@dataclass
class AdapterResult:
    adapter: str
    events: list[Event] = field(default_factory=list)
    ok: bool = False
    detail: str = ""


class Adapter(Protocol):
    name: str

    def scrape(self, source: Source) -> AdapterResult:
        """Fetch `source` and return whatever events were found.

        Must not raise for ordinary network/parse failures — return
        AdapterResult(ok=False, detail=...) instead so the orchestrator can
        fall through to the next adapter.
        """
        ...


def tag_from_source(events: Iterable[Event], source: Source) -> list[Event]:
    """Fill in provenance/geo fields the site itself didn't give us."""
    out = []
    for e in events:
        e.source_id = source.id
        if not e.state:
            e.state = source.state
        if not e.area:
            e.area = source.area
        if not e.city and source.area and "County" not in source.area:
            e.city = source.area
        out.append(e.normalize())
    return [e for e in out if e.is_valid()]

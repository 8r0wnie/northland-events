"""Load and query the source registry (sources/sources.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "sources" / "sources.yaml"


@dataclass
class Source:
    id: str
    name: str
    url: str
    events_url: str
    category: str
    state: str
    area: str
    platform: str
    status: str
    notes: str = ""
    trust: str = ""          # high | low — set explicitly, else derived from category
    refresh: str = ""        # "local" = site blocks CI's datacenter IPs; scrape it from
                             # the owner's machine (main.py refresh-local) and let CI
                             # carry the cached events forward
    timefix: str = ""        # per-site time quirk an adapter corrects, e.g. "utc-wall"
                             # (jsonld: the site's UTC instant is the real wall time)

    @property
    def target(self) -> str:
        """Where a scrape should start: the events page if known, else the root."""
        return self.events_url or self.url

    @property
    def trust_tier(self) -> str:
        """A single event from a 'high' source is trusted; a single event from a
        'low' source (aggregators, hard-to-verify origins) goes to review."""
        if self.trust in ("high", "low"):
            return self.trust
        return "low" if self.category in ("social", "aggregator") else "high"


def load_sources(path: Path = REGISTRY_PATH) -> list[Source]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[Source] = []
    for row in data.get("sources", []):
        out.append(Source(
            id=row["id"],
            name=row.get("name", row["id"]),
            url=row["url"].rstrip("/"),
            events_url=(row.get("events_url") or "").strip(),
            category=row.get("category", ""),
            state=row.get("state", ""),
            area=row.get("area", ""),
            platform=(row.get("platform") or "").strip(),
            status=row.get("status", "todo"),
            notes=row.get("notes", ""),
            trust=(row.get("trust") or "").strip(),
            refresh=(row.get("refresh") or "").strip(),
            timefix=(row.get("timefix") or "").strip(),
        ))
    return out

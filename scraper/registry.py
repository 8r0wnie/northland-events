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

    @property
    def target(self) -> str:
        """Where a scrape should start: the events page if known, else the root."""
        return self.events_url or self.url


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
        ))
    return out


def save_sources(sources: list[Source], path: Path = REGISTRY_PATH) -> None:
    """Rewrite the registry (used by the triage tool). Loses comments — keep a copy in git."""
    payload = {"sources": [
        {
            "id": s.id, "name": s.name, "url": s.url, "events_url": s.events_url,
            "category": s.category, "state": s.state, "area": s.area,
            "platform": s.platform, "status": s.status, "notes": s.notes,
        }
        for s in sources
    ]}
    path.write_text(yaml.safe_dump(payload, sort_keys=False, width=200, allow_unicode=True), encoding="utf-8")

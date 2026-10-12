"""Edit fields of one source in sources/sources.yaml without losing comments.

Each source is a one-line YAML flow mapping (`- {id: x, name: "...", ...}`).
Rewriting the whole file with yaml.dump would drop every comment, so all
automated edits (including `main.py triage --write`) go through this.

    python srcedit.py ely-mn platform=ics events_url=https://www.ely.mn.us/calendar
    python srcedit.py tower-mn status=active platform=posts "notes=Triaged 2026-10: ..."
"""
from __future__ import annotations

import json
import re
import sys

import yaml

from registry import REGISTRY_PATH

FIELD_ORDER = ["id", "name", "url", "events_url", "category", "state", "area",
               "platform", "status", "trust", "refresh", "timefix", "notes"]


def _fmt(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def set_fields(source_id: str, **fields: str) -> None:
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    # [^\n]* and [ \t]* (not .*\s*$) so the match never eats the line break
    # or the blank separator line that follows an entry
    line_re = re.compile(r"^(?P<indent>[ \t]*- )\{id: " + re.escape(source_id)
                         + r",[^\n]*\}[ \t]*$", re.M)
    m = line_re.search(text)
    if not m:
        raise SystemExit(f"source {source_id!r} not found as a one-line entry")
    row = yaml.safe_load(m.group(0).strip()[2:])
    for k, v in fields.items():
        if k == "id":
            raise SystemExit("refusing to change id")
        row[k] = v
    keys = [k for k in FIELD_ORDER if k in row] + [k for k in row if k not in FIELD_ORDER]
    parts = []
    for k in keys:
        v = row[k]
        parts.append(f"{k}: {v}" if k in ("id", "category", "state", "status") and v
                     and re.fullmatch(r"[A-Za-z0-9_-]+", str(v)) else f"{k}: {_fmt(str(v or ''))}")
    new_line = m.group("indent") + "{" + ", ".join(parts) + "}"
    text = text[:m.start()] + new_line + text[m.end():]
    yaml.safe_load(text)  # never write a file that doesn't parse
    REGISTRY_PATH.write_text(text, encoding="utf-8")


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    sid, pairs = argv[0], argv[1:]
    fields = {}
    for p in pairs:
        k, _, v = p.partition("=")
        fields[k.strip()] = v
    set_fields(sid, **fields)
    print(f"updated {sid}: {', '.join(fields)}")


if __name__ == "__main__":
    main(sys.argv[1:])

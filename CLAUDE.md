# Northland Events — project context

Public community events calendar for the **Duluth DMA**: Northern Minnesota,
Northwest Wisconsin, and Ironwood MI. Neutral branding — **not** tied to any
business.

This is a standalone project. It shares nothing with any other project. Run
Claude Code from this directory (`o:\Projects\Regional Event Scraper`) so it gets
its own session, memory, and history.

Repo: https://github.com/8r0wnie/northland-events (public)

## Documentation map — read the relevant doc before changing that area

| doc | holds |
|-----|-------|
| `CLAUDE.md` (this file) | rules, invariants, commands, doc-sync checklist, known issues |
| `docs/ARCHITECTURE.md` | pipeline, every data file (writer, reader, committed?), event schema, verification, recurrence, tabling, site pages |
| `docs/ADAPTERS.md` | every adapter: endpoint, parsing details, gotchas, how to add one |
| `docs/SOURCES.md` | `sources.yaml` fields + statuses, triage workflow, revisit list, CI-flaky sources |
| `docs/DEPLOY.md` | GitHub Actions, Cloudflare Workers, Google Site, private pages + passphrases |
| `moderation/README.md` | review-queue / `decisions.csv` workflow |
| `docs/sources-appendix.md` | the original seed list, as received (frozen — don't edit) |
| `README.md` | public-facing overview — keep it short, link to `docs/` |

`sources/sources.yaml` is the source of truth for sources. Per-source triage
findings go in that entry's `notes:` field, not in docs.

## Rules (agreed with the owner — do not relax without asking)

1. **Public data only.** No credentialed logins (incl. Facebook/Instagram), no
   API keys that need an account unless the owner approves, no IP or
   fingerprint rotation to defeat blocks. 2s per-domain crawl delay
   (`fetch.CRAWL_DELAY`). A site that blocks us gets `status: blocked`, not a
   workaround. (Rotating the User-Agent on a *retry* is the one existing
   exception — keep it limited to that.)
2. **All free tier.** GitHub Actions (public repo), Cloudflare Workers static
   assets, Google Sites. No paid services, no build step for the site.
3. **Neutral public calendar.** Nothing on `index.html` names or favors a
   business. Owner-specific tools (PBS North tabling list, trends dashboard,
   review queue) live on separate, unlinked, `noindex`, passphrase-gated pages
   and never leak into public filters or UI.
4. **Never publish unverified social/aggregator events.** Single-source
   low-trust events go to the review queue, never straight to `events.json`.
5. **Don't destroy accumulated state.** `site/data/event_history.json` is an
   ever-growing archive (never trim it, never regenerate it from scratch).
   `scraper/output/source_cache.json` and `moderation/decisions.csv` are also
   hand-off state. Change them only through the code paths that own them.
6. **`scrape --only` never writes `site/data/`.** Partial runs must not
   overwrite published data. Keep it that way.
7. **Data is committed by CI.** Local full scrapes are for testing. If you do
   commit one, `git pull` first — CI pushes a data commit every day, so local
   `main` goes stale fast.
8. **Sequencing:** coverage and data quality first, UI polish second.

## Working on the scraper

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r scraper/requirements.txt
python -m playwright install chromium
git pull --ff-only                       # CI commits data daily
cd scraper
python main.py list
python main.py triage --only <id,id>     # probe sources, see what yields events
python main.py scrape --only <id,id>     # summary only, does NOT write site/data/
python main.py scrape                    # all status: active sources -> ../site/data/
cd ../site && python -m http.server 8000 # preview http://localhost:8000
```

Windows note: the scraper reconfigures stdout to UTF-8; run child processes with
`PYTHONIOENCODING=utf-8` when calling pieces directly.

Caution: `triage --write` rewrites `sources.yaml` through
`registry.save_sources`, which **drops YAML comments and the `trust:` field**.
Edit `sources.yaml` by hand instead.

## Architecture in one paragraph

`scraper/main.py scrape` loads active sources from `sources/sources.yaml`, runs
each through its pinned adapter (or the generic chain), carries a source's
cached events forward if it comes back empty (up to 6 days), normalizes and
infers categories (`models.py`), groups duplicates and applies trust rules
(`verify.py`), tags tabling candidates (`tabling.py`), files events into the
year-over-year archive (`recurrence.py`), and writes `site/data/*.json`.
GitHub Actions runs this daily (~05:20 America/Chicago, plus
`workflow_dispatch`) and commits the data. Cloudflare Workers serves `site/`
and redeploys on every push. A Google Site iframes it as the stable public
address. Details: `docs/ARCHITECTURE.md`.

**Live URLs:** calendar https://northland-events.cbrown21.workers.dev ·
public front door https://sites.google.com/view/northland-regional-events/home
(it's `workers.dev`, not `pages.dev` — see `docs/DEPLOY.md`).

## Adapters (`scraper/adapters/`)

Generic chain, in order (first with events wins): `tribe`, `weblink`,
`civicplus`, `revize`, `mycalendar`, `webcalendar`, `govoffice`, `heygov`,
`jsonld`, `ics`, `chamberorganizer`, `chambermaster`.

Pin-only (set `platform:` in `sources.yaml`): `simpleview`, `tribehtml`,
`allevents`, `sidearm`, `ovationtix`, `decc`, `eventscalendar`, `spektrix`,
`birkie`, `northhouse`. Adapters that drive a headless browser must stay
pin-only.

Per-adapter endpoints, parsing details and gotchas: `docs/ADAPTERS.md`.

## Before you finish any change — doc-sync checklist

- New or removed adapter → `adapters/__init__.py`, the lists above, a section
  in `docs/ADAPTERS.md`, and the adapter table in `README.md`.
- New data file, new event field, or change to what CI commits →
  `docs/ARCHITECTURE.md` and the `git add` list in `.github/workflows/scrape.yml`.
- New page, passphrase, or deploy change → `docs/DEPLOY.md`.
- Source triaged → its `notes:` in `sources.yaml`; reusable patterns →
  `docs/SOURCES.md`.
- New owner rule or scope decision → the Rules section above.
- Finished a planned item or fixed a known issue → update the lists below.
- Verify before calling it done: `scrape --only` on the affected sources; for
  site changes, preview locally and check the browser console for errors.

## Health check (start of a session)

```bash
git pull --ff-only
cd scraper && python -c "import json;r=json.load(open('output/last_run.json'));print(r['when'],r['published'],r['queued']);print([s['source'] for s in r['sources'] if s['events']==0])"
```

Compare `published` with the trend in `site/data/scrape_stats_history.json`.
A source that used to produce and now shows 0 is a regression to investigate.

## Planned

- Google Form → Sheet CSV `manual` adapter for Facebook-only / word-of-mouth
  events (`docs/DEPLOY.md` §5).
- Explore Minnesota / Iron Range sources (still no coverage).
- Revisit the `no-calendar` / `todo` candidates listed in `docs/SOURCES.md`.

## Known issues

- **Coverage decline:** published events fell from ~2,650 (2026-09-05) to
  ~1,870 (2026-10-11), and 26 active sources returned 0 on 2026-10-11. Some are
  small gov calendars with nothing upcoming. Others used to produce:
  `spooner-chamber`, `ely-chamber`, `norshor-playhouse`,
  `duluth-art-institute`, `earth-rider`. Partly seasonal; needs investigation.
- Some Tribe/WordPress and headless-browser sources intermittently return
  nothing from GitHub Actions IPs (datacenter rate-limiting). The carry-forward
  cache only masks outages up to 6 days. See `docs/SOURCES.md`.
- `allevents` listings have a date but no time, so those events are all-day.
- `registry.save_sources` drops comments and `trust:` (see caution above).
- `review.html`, `tabling.html` and `recurring.html` also accept `__dev__` as a
  passphrase. The gate is casual-view privacy only (the JSON is public), but
  remove the bypass if that ever matters.
- `review.html` still uses the default passphrase `northland-admin`.

# Northland Events

A public community events calendar for the **Duluth DMA** — Northern Minnesota,
Northwest Wisconsin, and Ironwood, MI.

- **Scraper** (`scraper/`) — Python. Pulls events from ~135 active tourism, chamber,
  government, venue, and aggregator sites listed in `sources/sources.yaml`. Runs daily via
  GitHub Actions.
- **Site** (`site/`) — static month/week calendar (FullCalendar) that reads
  `site/data/events.json`. Deployed to Cloudflare Workers (static assets); embedded in a Google
  Site for a stable public URL.

## How it fits together

```
GitHub Actions (daily cron)
  └─ python scraper/main.py scrape
       ├─ reads sources/sources.yaml
       ├─ per source: tries adapters (Tribe API → JSON-LD → iCal → bespoke)
       ├─ fuzzy-merges duplicates, holds low-trust events for review
       └─ writes site/data/events.json  ── committed back to the repo
                                            │
Cloudflare Workers ─ auto-deploys site/ on push ─ https://northland-events.cbrown21.workers.dev
                                            │
Google Site ─ <iframe> embed of the Pages URL ─ stable public address
```

## Local development

```bash
python -m venv .venv
.venv/Scripts/activate                 # Windows;  source .venv/bin/activate elsewhere
pip install -r scraper/requirements.txt
python -m playwright install chromium

cd scraper
python main.py list                    # show the source registry
python main.py triage --only visit-duluth,ely-chamber   # probe sources, see what yields events
python main.py triage --write          # flip working sources to status: active
python main.py scrape                  # run all active sources -> ../site/data/events.json
python main.py scrape --only visit-duluth
```

Preview the site:

```bash
cd site && python -m http.server 8000   # open http://localhost:8000
```

## Source registry (`sources/sources.yaml`)

One entry per site. Key fields:

| field        | meaning |
|--------------|---------|
| `status`     | `todo` (needs triage) · `active` (scraped daily) · `no-calendar` · `blocked` · `broken` |
| `events_url` | the calendar page, once found (blank = scraper starts at the site root) |
| `platform`   | detected CMS (`wordpress-tribe`, `chambermaster`, `civicplus`, …) — pins the adapter |

## Adapters (`scraper/adapters/`)

30 adapters. Generic chain, tried in order: `tribe`, `weblink`, `civicplus`,
`revize`, `mycalendar`, `webcalendar`, `govoffice`, `heygov`, `jsonld`,
`microdata`, `simplecalendar`, `ics`, `chamberorganizer`, `chambermaster`.
Pin-only via `platform:`: `simpleview`, `tribehtml`, `allevents`, `sidearm`,
`ovationtix`, `decc`, `eventscalendar`, `spektrix`, `birkie`, `northhouse`,
`eventbrite`, `dpcalendar`, `exploremn`, `squarespace`, `nps`, and `posts`.
`posts` infers events from news posts and page text, and those events are
reviewed before they publish.

## Further docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — pipeline, data files, event schema, verification, recurrence
- [docs/ADAPTERS.md](docs/ADAPTERS.md) — what each adapter talks to, gotchas, how to add one
- [docs/SOURCES.md](docs/SOURCES.md) — registry fields, statuses, triage workflow
- [docs/DEPLOY.md](docs/DEPLOY.md) — GitHub Actions, Cloudflare, Google Site
- [moderation/README.md](moderation/README.md) — the review queue
- [CLAUDE.md](CLAUDE.md) — project rules and working checklist

## Scope boundaries

Public data only. No credentialed logins (incl. Facebook/Instagram), no IP or
fingerprint rotation to defeat blocks. Per-domain crawl delay is 2s. Events the
scraper can't reach will be added through a Google Form → Sheet (planned).

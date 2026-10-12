# Architecture

How a daily run turns ~85 active sources into the published calendar, and
every file that pipeline reads or writes. Keep this in sync with
`scraper/main.py` and `.github/workflows/scrape.yml`.

## Pipeline (`python main.py scrape`)

```
sources/sources.yaml (status: active)
  └─ per source: adapter (pinned `platform:` or the generic chain)
       ├─ keep events in [now − 1 day, now + 200 days]       HORIZON_DAYS
       ├─ non-empty → store in source_cache.json
       └─ empty     → reuse cached events if ≤ 6 days old     CACHE_MAX_AGE_DAYS
  └─ Event.normalize()          models.py   clean text, infer category
  └─ group_events + classify    verify.py   fuzzy dedup, trust rules, decisions.csv
  └─ classify_tabling           tabling.py  PBS North tabling flag (title only)
  └─ recurrence                 recurrence.py  archive + annual-series view + stats
  └─ write site/data/*.json, scraper/output/last_run.json
```

`--only id,id` runs the same scrape and prints a summary, but returns before
writing anything under `site/data/` (it does still update `source_cache.json`).

## Data files

| file | written by | read by | committed by CI | notes |
|------|-----------|---------|-----------------|-------|
| `sources/sources.yaml` | hand (or `triage --write`) | scraper | no (hand commits) | source of truth for sources |
| `site/data/events.json` | `scrape` | `index.html`, `tabling.html` | yes | rolling window, overwritten each run |
| `site/data/review_queue.json` | `scrape` | `review.html` | yes | pending low-trust items |
| `moderation/decisions.csv` | `scrape` appends blank rows; admin edits via `review.html` Export | `verify.py` | yes | `key,decision,title,date,source,note` |
| `site/data/decisions.csv` | `scrape` (copy of the above) | `review.html` | yes | read-only published copy |
| `site/data/event_history.json` | `recurrence.update_history` | `recurrence` | yes | **never trimmed**, the only long-term memory |
| `site/data/recurring_events.json` | `recurrence.build_recurring_view` | `recurring.html` | yes | `confirmed` / `likely` series, regenerated |
| `site/data/scrape_stats_history.json` | `recurrence.append_stats_snapshot` | `recurring.html` | yes | one row per day, capped at 730 |
| `scraper/output/source_cache.json` | `scrape` | `scrape` | yes | last-good events per source |
| `scraper/output/last_run.json` | `scrape` | humans / health check | yes | counts + per-source detail |

If you add a file CI should keep, add it to the `git add` line in
`.github/workflows/scrape.yml` — otherwise it's lost after every run.

## Event schema (`events.json` → `events[]`)

From `models.Event.to_dict()` plus fields added downstream:

| field | meaning |
|-------|---------|
| `source_id` | primary source id |
| `title`, `description` | cleaned text |
| `start`, `end` | ISO local (America/Chicago) datetimes, no offset |
| `all_day` | bool |
| `venue`, `address`, `city`, `state` (MN/WI/MI), `area` | `area` is the filter label, inherited from the source unless the adapter overrides |
| `url`, `image`, `price` | `price` is free-form |
| `category` | one of `community`, `sports-rec`, `arts`, `family`, `food-drink`, `music`, `holiday`, `education`, `other` |
| `scraped_at` | UTC ISO timestamp |
| `fingerprint` | sha1 of normalized title + start date + city, 16 hex chars |
| `verification` | `multi-source`, `trusted-single`, or `reviewed` |
| `sources`, `source_count` | `[{name,url}]` of every source in the merged group |
| `tabling`, `tabling_reasons` | PBS North flag — only `tabling.html` uses it |

Top level: `generated`, `count`, `areas`, `categories`, `events`.

## Categorization (`models.py`)

Adapters set a category when the platform gives one (Tribe's are mapped and
left alone). Events left `other`, or at the coarse gov default `community`,
go through `infer_category(title, desc, fallback)`: keyword table
`_CATEGORY_RULES`, title checked before description. Add keywords there, not
in adapters.

## Verification & review queue (`verify.py`)

- **Same event:** rapidfuzz `token_set_ratio` ≥ 78 on normalized titles
  (`TITLE_RATIO`), start dates within ±1 day, and venues not clearly different
  (venue ratio ≥ 45 when both are present).
- **Trust tier** (`registry.Source.trust_tier`): `trust:` in `sources.yaml` if
  set, else `low` for `category: social`/`aggregator`, else `high`.
- **Confirmed (published):** 2+ distinct sources, or 1 high-trust source.
- **Held:** a single low-trust source. Written to `review_queue.json` and a
  blank row added to `moderation/decisions.csv`, keyed by fingerprint.
- `decision=approve` → published with `verification: "reviewed"`;
  `reject` → dropped permanently; blank → stays queued.

Workflow for the admin: `moderation/README.md`.

## Recurrence & trends (`recurrence.py`)

`events.json` forgets everything outside its window. Each run, every published
event is filed into `event_history.json` under a **series key** (normalized
title with years stripped, plus area). Occurrences whose dates fall within 21
days of each other count as one edition.

- **Confirmed annual:** seen in 2+ editions spaced 300–400 days apart. Weekly
  or monthly meetups can't qualify regardless of title.
- **Likely annual:** a single edition whose title says so ("45th Annual…").
- `recurring_events.json` is the UI-ready view, with a predicted next date.
- `scrape_stats_history.json` holds one daily snapshot (raw, merged,
  published, queued, active sources, category mix) for the trend charts.

The long comment block in `recurrence.py` is the authoritative description of
the clustering logic — update it with any change.

## Tabling flag (`tabling.py`)

Scope set by PBS North: fairs/festivals/community celebrations,
expos/health fairs/resource days, parades/large public gatherings. Farmers
markets are excluded on purpose. Matching uses the **title only**, because
descriptions mention venue names like "Bayfront Festival Park". It only tags
events. It never filters or changes the public calendar.

## Site (`site/`, static, no build)

| page | audience | data | gate |
|------|----------|------|------|
| `index.html` | public | `events.json` | none — FullCalendar 6.1.15 (jsDelivr), month/week/list, area + category filters, search |
| `review.html` | admin | `review_queue.json`, `decisions.csv` | passphrase |
| `tabling.html` | PBS North | `events.json` (`tabling`) | passphrase, `noindex` |
| `recurring.html` | owner | `recurring_events.json`, `scrape_stats_history.json` | passphrase, `noindex` |

Passphrase gates compare a SHA-256 (`PASS_HASH` in each file). They keep
casual viewers out, not determined ones: the JSON files are public. Current
passphrases and how to change them: `docs/DEPLOY.md`.

## Fetching (`fetch.py`)

- 2s minimum between requests to the same host (`CRAWL_DELAY`).
- Retries `403/429/500/502/503/504` and connection errors twice, with backoff,
  jitter and a different User-Agent on retry. Other statuses fail fast.
- Optional headless Chromium (Playwright) for JS-rendered pages; adapters that
  rely on it are pin-only.

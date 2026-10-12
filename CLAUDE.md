# Northland Events — project context

Public community events calendar for the **Duluth DMA**: Northern Minnesota,
Northwest Wisconsin, and Ironwood MI. Neutral content, **not** tied to any
business; styled to the PBS style guide (no logos) because it will live on the
PBS North website.

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
| `moderation/README.md` | owner decision files (review + annual), written by `/admin` |
| `worker/index.js` | the admin API (Cloudflare Worker) — its header comment is the endpoint reference |
| `docs/sources-appendix.md` | the original seed list, as received (frozen — don't edit) |
| `README.md` | public-facing overview — keep it short, link to `docs/` |

`sources/sources.yaml` is the source of truth for sources. Per-source triage
findings go in that entry's `notes:` field, not in docs.

## Rules (agreed with the owner — do not relax without asking)

1. **Public data only.** No credentialed logins (incl. Facebook/Instagram), no
   API keys that need an account unless the owner approves, no IP or
   fingerprint rotation to defeat blocks. 2s per-domain crawl delay
   (`fetch.CRAWL_DELAY`). A site that blocks us gets `status: blocked`, not a
   workaround. Two narrow exceptions, both owner-approved:
   - Rotating the User-Agent on a *retry*.
   - **Relay, owner decision 2026-10-12.** Sources marked `refresh: relay`
     refuse GitHub's datacenter IPs, so CI fetches them through the site's
     own Cloudflare Worker (`/api/relay`). That's one fixed origin, the same
     2 s crawl delay, and only hosts the registry lists.
   Never extend this to proxies, IP pools, stealth browsers or
   challenge-solving. Never add a source to the relay without the owner.
2. **All free tier.** GitHub Actions (public repo), Cloudflare Workers static
   assets, Google Sites. No paid services, no build step for the site.
3. **Neutral public calendar.** Nothing on `index.html` names or favors a
   business. The *look* follows the PBS style guide (colours and type in
   `site/theme.css`, owner decision 2026-10-12: the calendar will live on
   the PBS North website). Logos stay out until the owner says otherwise. Owner-specific tools (PBS North tabling list, trends dashboard,
   review queue) live on separate, unlinked, `noindex`, passphrase-gated pages
   and never leak into public filters or UI.
4. **Source tiers decide what publishes** (owner-set on `/admin`):
   - *trusted* publishes alone.
   - *source* needs a second independent website, or the owner's approval.
   - *potential* always goes to review, and never counts as a witness.
   Social and aggregator sources default to *source*, so a lone
   social/aggregator event is never published unverified. Never change a
   tier in code without the owner.
5. **Don't destroy accumulated state.** `site/data/event_history.json` is an
   ever-growing archive (never trim it, never regenerate it from scratch).
   `scraper/output/source_cache.json` and `scraper/output/source_history.json`
   are hand-off state, and `moderation/*.csv` is owner-only (the admin page
   writes it, and CI never does). Change them only through the code paths
   that own them.
6. **`scrape --only` never writes `site/data/`.** Partial runs must not
   overwrite published data. Keep it that way.
7. **Data is committed by CI.** Local full scrapes are for testing. If you do
   commit one, `git pull` first — CI pushes a data commit every day, so local
   `main` goes stale fast.
8. **Sequencing:** coverage and data quality first, UI polish second.
9. **Docs ship with the change — every commit, every push.** Keeping these
   docs current is part of the process, not a follow-up. Run the doc-sync
   checklist below before every commit, and put the doc updates in the
   *same* commit as the code. A commit that changes code with no doc change
   needs a stated reason. The hook `.claude/hooks/doc_sync_reminder.py`
   (registered in `.claude/settings.json`) re-surfaces this on every
   `git commit` / `git push` and warns when code changed without docs.
10. **Times are Central wall time.** Use `models.parse_dt` / `from_epoch`.
    Never strip an offset or call a bare `datetime.fromtimestamp()`, because CI
    runs in UTC. See "Time handling" in `docs/ARCHITECTURE.md`.
11. **Inferred events are reviewed.** Events the `posts` adapter reads out of
    prose carry `inferred: true`. A lone inferred event goes to the review
    queue. Independence is counted by website, not registry entry.

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
python main.py refresh-local             # `refresh: local` sources -> cache only (owner's PC)
python srcedit.py <id> key=value ...     # edit one sources.yaml entry, keeps comments
python tests/test_posts.py && python tests/test_tiers.py && python tests/test_descriptions.py   # regression tests (also run in CI)
cd ../site && python -m http.server 8000 # preview http://localhost:8000
```

Windows note: the scraper reconfigures stdout to UTF-8; run child processes with
`PYTHONIOENCODING=utf-8` when calling pieces directly.

Edit `sources.yaml` by hand or with `srcedit.py`. `triage --write` uses
`srcedit` too, so comments and every field survive. Never yaml-dump the whole
file.

## Architecture in one paragraph

`scraper/main.py scrape` loads active sources from `sources/sources.yaml`, runs
each through its pinned adapter chain (or the generic chain), trims
high-frequency series to 60 days, carries a source's cached events forward
if it comes back empty (6 days, or 10 for `refresh: local`), normalizes and
infers categories (`models.py`), groups duplicates and applies trust rules
(`verify.py`), tags tabling candidates (`tabling.py`), files events into the
year-over-year archive (`recurrence.py`), and writes `site/data/*.json`.
GitHub Actions runs this daily (~05:20 America/Chicago, plus
`workflow_dispatch`, which the admin page's "Rebuild now" calls) and commits
the data. Cloudflare Workers serves `site/` and redeploys on every push. The
same Worker runs `worker/index.js` for `/api/*`: the admin backend, which
commits owner decisions to the repo (`docs/DEPLOY.md` §9). A Google Site iframes it as the stable public
address. Details: `docs/ARCHITECTURE.md`.

**Live URLs:** calendar https://northland-events.cbrown21.workers.dev ·
public front door https://sites.google.com/view/northland-regional-events/home
(it's `workers.dev`, not `pages.dev` — see `docs/DEPLOY.md`).

## Adapters (`scraper/adapters/`)

Generic chain, in order (first with events wins): `tribe`, `weblink`,
`civicplus`, `revize`, `mycalendar`, `webcalendar`, `govoffice`, `heygov`,
`jsonld`, `microdata`, `simplecalendar`, `ics`, `chamberorganizer`,
`chambermaster`.

Pin-only (set `platform:` in `sources.yaml`): `simpleview`, `tribehtml`,
`allevents`, `sidearm`, `ovationtix`, `decc`, `eventscalendar`, `spektrix`,
`birkie`, `northhouse`, `eventbrite`, `dpcalendar`, `exploremn`,
`squarespace`, `nps`, `evvnt`, `posts`. Adapters that drive a headless browser, and
the `posts` text-inference fallback, must stay pin-only. `platform:` can
chain adapters, e.g. `govoffice,posts`. Every active source is pinned.

Per-adapter endpoints, parsing details and gotchas: `docs/ADAPTERS.md`.

## Before you finish any change — doc-sync checklist

- New or removed adapter → `adapters/__init__.py`, the lists above, a section
  in `docs/ADAPTERS.md`, and the adapter table in `README.md`.
- New data file, new event field, or change to what CI commits →
  `docs/ARCHITECTURE.md` and the `git add` list in `.github/workflows/scrape.yml`.
- New page, passphrase, deploy change, or new `/api` endpoint → `docs/DEPLOY.md`
  (§9 for the admin API) and the header comment of `worker/index.js`.
- Source triaged → its `notes:` in `sources.yaml`; reusable patterns →
  `docs/SOURCES.md`.
- New owner rule or scope decision → the Rules section above.
- Finished a planned item or fixed a known issue → update the lists below.
- Touched parsing or time handling → add a case to `scraper/tests/` and run
  the tests, with `TZ=UTC` too.
- Verify before calling it done: `scrape --only` on the affected sources,
  and compare a few event times with the site's own page. For site changes,
  preview locally and check the browser console for errors.

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
- **Meta Graph API organizer opt-in** for Facebook-only Pages: an
  owner-approved Meta developer app plus app review. See `docs/SOURCES.md`
  (Facebook). Not started; needs the owner's account.
- **Blocked, broken and dead sources:** the owner wants these addressed, with
  the list in `docs/SOURCES.md`. Use only legitimate routes (Rule 1).

## Known issues

- **Coverage history:** published events fell from ~2,650 (2026-09-05) to
  ~1,870 (2026-10-11). Causes found on 2026-10-11:
  - 7 sources 403 GitHub IPs, now `refresh: local`.
  - Several sites moved their calendars (Ely Chamber went to MEC, GovOffice
    cities to Google Calendar, Spirit Mountain to /events/).
  - The iCal adapter ignored RRULE, so recurring meetings vanished.
  - The rest is seasonal.
  Watch the trend after the 2026-10-11 push.
- **CI runtime:** 135 sources, several of them browser-driven, plus posts
  link-following. A full local run took 21 minutes on 2026-10-11, and the CI
  timeout is 90 minutes. Check run
  times; if they get close to the limit, parallelize across domains.
- `posts` events are heuristic. Expect some noise in the review queue, and
  add regression cases when you fix one.
- `allevents` listings have a date but no time, so those events are all-day.
- Explore Minnesota answers one search call per page load. The adapter makes
  exactly one call per run. Don't loop it.
- `tabling.html` and `recurring.html` use a client-side passphrase gate:
  casual-view privacy only, since the JSON is public. The `__dev__` bypass
  was removed on 2026-10-12.
- The admin backend needs two Cloudflare secrets, `GITHUB_TOKEN` (a
  fine-grained PAT) and `ADMIN_PASSWORD`, set by the owner (`docs/DEPLOY.md`
  §9). The PAT expires, so renew it before then.

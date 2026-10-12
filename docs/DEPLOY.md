# Deploying Northland Events (all free tier)

## 1. GitHub repo

```bash
cd "o:/Projects/Regional Event Scraper"
git init
git add -A
git commit -m "Initial scaffold"
# create a PUBLIC repo named northland-events on github.com, then:
git remote add origin https://github.com/<you>/northland-events.git
git branch -M main
git push -u origin main
```

Public repo = unlimited free GitHub Actions minutes.

## 2. GitHub Actions (the daily scraper)

Already configured in [.github/workflows/scrape.yml](../.github/workflows/scrape.yml):

- runs every day ~05:20 America/Chicago, and on demand via the **Actions → Scrape
  events → Run workflow** button
- installs deps, runs `python main.py scrape`, commits the refreshed
  `site/data/events.json` back to `main`

Nothing to configure — `GITHUB_TOKEN` is provided automatically and the workflow
has `contents: write`.

## 3. Cloudflare (the website) — DONE, live at
**https://northland-events.cbrown21.workers.dev**

Cloudflare's dashboard has folded classic "Pages" into a unified **Workers**
Git-connect flow, so this deploys as a Workers **static assets** project rather
than a Pages project — same free tier, same auto-redeploy-on-push behavior,
different URL shape (`workers.dev` instead of `pages.dev`).

What made it work: [`wrangler.toml`](../wrangler.toml) at the repo root —

```toml
name = "northland-events"
compatibility_date = "2026-09-04"

[assets]
directory = "./site"
```

Setup steps taken (for reference / redoing elsewhere):
1. dash.cloudflare.com → **Compute** → **Workers** → **Create** → connect the
   `northland-events` GitHub repo.
2. Build command: blank. Deploy command: `npx wrangler deploy` (default).
3. Deploy — Cloudflare reads `wrangler.toml` and serves `site/` as static assets.

Every push to `main` — including the daily data commit from GitHub Actions —
auto-redeploys. No CLI, no build step, nothing to re-run.

Cloudflare's static-asset server auto-redirects `/review.html` → `/review`
(307, clean-URL convention) — both paths work, it's cosmetic.

**Verify:** open the URL, confirm the calendar loads with today's event count
in the footer stamp. Open `/admin`, unlock with the admin passphrase, confirm
the queue loads. (Confirmed working 2026-09-04.)

## 4. Google Site (the stable public address) — DONE, live at
**https://sites.google.com/view/northland-regional-events/home**

1. [sites.google.com](https://sites.google.com) → **+ Blank** (new site).
2. Name the site (top left, e.g. "Northland Events").
3. Right-hand **Insert** panel → **Embed** → **By URL** tab →
   `https://northland-events.cbrown21.workers.dev` → **Insert**.
4. Drag the embed's corner handles to fill the page — Google Sites gives it a
   small default box.
5. Top right → **Publish**. Pick a web address (e.g. `sites.google.com/view/northland-events`
   or a custom one if you have Workspace).
6. Share that Google Sites URL — that's the one to give out; it never changes
   even if the Cloudflare project ever gets rebuilt.

This is the address to hand out publicly. It just iframes the Cloudflare Worker
URL above, so every daily data refresh and every UI change (color-coding,
search bar, etc.) shows up here automatically — nothing to re-publish on this
end unless the embedded URL itself changes.

If you ever want a page for the review queue too, repeat with
`https://northland-events.cbrown21.workers.dev/admin` as a second page
(mark it unlisted in the site's nav, or don't add it to the menu — it's still
passphrase-gated).

## 5. Manual events (Facebook-only, word-of-mouth) — later

1. Google Form with fields: Title, Date, Start time, End time, Venue, City,
   Category, Description, Link.
2. Form responses → Google Sheet → **File → Share → Publish to web** as CSV.
3. Add a `manual` adapter that reads that CSV URL and a registry entry for it.

## 6. PBS North tabling list — DONE, private

**https://northland-events.cbrown21.workers.dev/tabling.html** — passphrase-gated
(client-side passphrase gate), passphrase **`pbsnorth-tabling`**. Not linked
from anywhere public and marked `noindex, nofollow`.

Lists only upcoming events flagged as a good tabling opportunity for PBS North —
fairs/festivals, expos/health fairs/resource days, and parades/large public
gatherings (farmers markets excluded on purpose). The flag is computed once
per scrape in `scraper/tabling.py` from the event *title* and stored on each
event in `site/data/events.json` as `tabling` / `tabling_reasons`. It never
appears on the public calendar or its filters — this page is the only consumer.

To change the passphrase: compute `sha256("yournewpassphrase")` (browser
console snippet is in a comment at the top of `tabling.html`'s script) and
swap `PASS_HASH` in [`site/tabling.html`](../site/tabling.html).

## 7. Annual events & trends dashboard — DONE, private

**https://northland-events.cbrown21.workers.dev/recurring.html** — same
passphrase-gate mechanism, passphrase **`northland-annual-trends`**. Not
linked from anywhere public and marked `noindex, nofollow`.

Tracks recurring events year-over-year and regional coverage trends over
time — `events.json` is only a rolling ~200-day window and remembers
nothing on its own, so `scraper/recurrence.py` files every published event
into a persistent archive (`site/data/event_history.json`, never trimmed)
each run. An event graduates to **confirmed** once it's been seen in two
separate years spaced ~a year apart; before that it can show as **likely**
from title context clues alone ("Nth Annual…"). A weekly/monthly meetup is
guarded against ever qualifying, however it's titled — see the long comment
in `recurrence.py` for the clustering logic. `site/data/scrape_stats_history.json`
is a capped daily snapshot (published count, category mix) feeding the
trend line and category chart; both start accumulating from the day this
shipped and get more useful over time, especially past the one-year mark.

To change the passphrase: same recipe as above, swap `PASS_HASH` in
[`site/recurring.html`](../site/recurring.html).

## 8. Local refresh for CI-blocked sources — owner's machine (superseded by §10)

No source uses this since 2026-10-12. The CI-blocked sites now go through the
Worker relay (§10). Kept for reference.

Some sites return HTTP 403 to GitHub Actions' datacenter IPs but answer
normally from home. They're marked `refresh: local` in
`sources/sources.yaml`; the list is in `docs/SOURCES.md`.
[`scripts/refresh_local.ps1`](../scripts/refresh_local.ps1) runs these steps:

1. `git pull --ff-only`
2. `python main.py refresh-local`, which scrapes only those sources and
   updates `scraper/output/source_cache.json`
3. commits and pushes that one file

The next CI run carries those events forward for up to 10 days
(`LOCAL_CACHE_MAX_AGE_DAYS`). Running it daily, or at least twice a week,
keeps them on the calendar.

Run it by hand:

```powershell
powershell -ExecutionPolicy Bypass -File "O:\Projects\Regional Event Scraper\scripts\refresh_local.ps1"
```

Schedule it daily at 04:45, before the 05:20 CI scrape. This is a one-time
setup on the owner's PC. It needs the repo's `.venv` and the Git credential
already used for pushes.

```powershell
schtasks /Create /TN "NorthlandEvents-LocalRefresh" /SC DAILY /ST 04:45 /F `
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"O:\Projects\Regional Event Scraper\scripts\refresh_local.ps1`""
```

Remove it with `schtasks /Delete /TN "NorthlandEvents-LocalRefresh" /F`. If the PC is off,
the run is skipped and CI keeps the last refresh for up to 10 days.


## 9. Admin backend (review queue, annual events, sources) — `/admin`

**https://northland-events.cbrown21.workers.dev/admin** (the old `/review.html` redirects here).

The same Cloudflare Worker that serves `site/` runs `worker/index.js` for
`/api/*` (`wrangler.toml`: `main = "worker/index.js"`). The admin page calls
it to:
- approve or reject queued events (`moderation/decisions.csv`),
- confirm or reject suspected annual events (`moderation/annual_decisions.csv`),
- change a source's tier or status, and add potential sources
  (`sources/sources.yaml`),
- start a scrape now (`workflow_dispatch` on `scrape.yml`).

Each save is one commit to `main`, made with a GitHub token held by the
Worker. The repo stays the single source of truth and keeps a full history
of every decision. Nothing runs on the owner's PC.

**One-time setup.** The Worker refuses every admin request until both
secrets exist.
1. **GitHub token.** github.com → Settings → Developer settings →
   Fine-grained tokens → Generate new token.
   - Repository access: *Only select repositories* → `northland-events`.
   - Permissions: **Contents: Read and write**, **Actions: Read and write**.
   - Expiration: up to one year. Put a renewal reminder on your calendar.
2. **Cloudflare secrets.** dash.cloudflare.com → Compute → Workers →
   `northland-events` → Settings → Variables and Secrets → Add. Choose
   type **Secret** for both:
   - `GITHUB_TOKEN` = the token from step 1
   - `ADMIN_PASSWORD` = the admin passphrase you'll type on `/admin`
     (pick a new one; `northland-admin` is retired)
3. Deploy: secrets apply on the next deploy, and any push to `main` triggers
   one. Or use the dashboard's **Deployments → Redeploy**.
4. Open `/admin` and unlock. If it says "admin API not configured", a secret
   is missing or misnamed.

**Notes:**
- Saves take effect at the next scrape. "Rebuild calendar now" starts one,
  which takes about 30 minutes.
- The passphrase is checked server-side (SHA-256, constant-time compare). A
  wrong one costs a short delay. The static JSON under `site/data/` is public
  either way, as it always was.
- If a save conflicts with a running scrape, the Worker retries. The scrape's
  own commit rebases with `-X ours`, so the owner's change wins.
- Local test: put `ADMIN_PASSWORD` and `GITHUB_TOKEN` in `.dev.vars`
  (git-ignored) and run `npx wrangler dev --var GITHUB_BRANCH:<test-branch>`
  against a throwaway branch, never `main`.


## 10. Relay for sites that refuse GitHub's servers — automatic, no PC

Some sites return HTTP 403 to GitHub Actions' datacenter IPs. They're marked
`refresh: relay` in `sources/sources.yaml` (list in `docs/SOURCES.md`). During
the daily scrape, requests to their hosts (plus any `relay_hosts:` extras,
e.g. `api.ovationtix.com` for NorShor) go to the site's own Worker at
`/api/relay`, which makes the request from Cloudflare. Plain requests and
the headless browser (via a Playwright route) both use it.

- **Owner decision, 2026-10-12** (CLAUDE.md Rule 1 exception). One fixed
  origin, the scraper's own 2 s per-domain delay, no rotation, no challenge
  solving. The Worker refuses any host that isn't on a `refresh: relay`
  entry.
- **Auth needs no secret.** CI sends its own short-lived `GITHUB_TOKEN`
  (`RELAY_TOKEN`) and run id (`RELAY_RUN`). The Worker checks with GitHub
  that this run of this repo is still in progress (the workflow grants
  `actions: read` for that) and reads the relay list with the same token.
  The relay works even before the admin secrets in §9 are set.
- **If a relayed site still refuses** (Cloudflare is a datacenter too), the
  source just comes back empty, and its cached events carry forward for 10
  days. Check the CI log for `relay` lines.
- **Local test:** `.dev.vars` with `RELAY_DEV=1`, run `wrangler dev`, then
  `RELAY_URL=http://127.0.0.1:8787/api/relay RELAY_TOKEN=<gh token> python main.py scrape --only <id>`.

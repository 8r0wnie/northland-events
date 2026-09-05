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
in the footer stamp. Open `/review.html`, unlock with the passphrase, confirm
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
`https://northland-events.cbrown21.workers.dev/review.html` as a second page
(mark it unlisted in the site's nav, or don't add it to the menu — it's still
passphrase-gated).

## 5. Manual events (Facebook-only, word-of-mouth) — later

1. Google Form with fields: Title, Date, Start time, End time, Venue, City,
   Category, Description, Link.
2. Form responses → Google Sheet → **File → Share → Publish to web** as CSV.
3. Add a `manual` adapter that reads that CSV URL and a registry entry for it.

## 6. PBS North tabling list — DONE, private

**https://northland-events.cbrown21.workers.dev/tabling.html** — passphrase-gated
(same mechanism as `/review.html`), passphrase **`pbsnorth-tabling`**. Not linked
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

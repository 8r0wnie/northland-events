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

## 3. Cloudflare Pages (the website)

1. Log in at [dash.cloudflare.com](https://dash.cloudflare.com).
2. Left sidebar → **Workers & Pages** → **Create** (top right).
3. **Pages** tab → **Connect to Git**.
4. Authorize Cloudflare for GitHub if asked, then pick the **`northland-events`**
   repo → **Begin setup**.
5. Build settings:
   - Project name: `northland-events` (this becomes the `.pages.dev` subdomain)
   - Production branch: `main`
   - Framework preset: **None**
   - Build command: *(leave blank)*
   - Build output directory: **`site`**
   - Root directory: *(leave as `/`)*
6. **Save and Deploy.** First deploy takes ~30 seconds.
7. You get **`https://northland-events.pages.dev`** — the calendar is at the
   root, the admin review queue at `/review.html` (passphrase-gated).

Every push to `main` — including the daily data commit from GitHub Actions —
auto-redeploys. No CLI, no build step, nothing to re-run.

**Verify:** open the `.pages.dev` URL, confirm the calendar loads with today's
event count in the footer stamp. Open `/review.html`, unlock with your
passphrase, confirm the queue loads.

## 4. Google Site (the stable public address)

1. [sites.google.com](https://sites.google.com) → **+ Blank** (new site).
2. Name the site (top left, e.g. "Northland Events").
3. Right-hand **Insert** panel → **Embed** → **By URL** tab →
   `https://northland-events.pages.dev` → **Insert**.
4. Drag the embed's corner handles to fill the page — Google Sites gives it a
   small default box.
5. Top right → **Publish**. Pick a web address (e.g. `sites.google.com/view/northland-events`
   or a custom one if you have Workspace).
6. Share that Google Sites URL — that's the one to give out; it never changes
   even if the Cloudflare project ever gets rebuilt.

If you ever want a page for the review queue too, repeat with
`https://northland-events.pages.dev/review.html` as a second page (mark it
unlisted in the site's nav, or don't add it to the menu — it's still
passphrase-gated).

## 5. Manual events (Facebook-only, word-of-mouth) — later

1. Google Form with fields: Title, Date, Start time, End time, Venue, City,
   Category, Description, Link.
2. Form responses → Google Sheet → **File → Share → Publish to web** as CSV.
3. Add a `manual` adapter that reads that CSV URL and a registry entry for it.

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

1. Create a free account at cloudflare.com.
2. **Workers & Pages → Create → Pages → Connect to Git** → pick the
   `northland-events` repo.
3. Build settings:
   - Framework preset: **None**
   - Build command: *(leave blank)*
   - Build output directory: **`site`**
4. Deploy. You get `https://northland-events.pages.dev`.

Every push to `main` (including the daily data commit from Actions) auto-deploys.
No CLI, no build step.

## 4. Google Site (the stable public address)

1. sites.google.com → new site.
2. **Insert → Embed → By URL** → `https://northland-events.pages.dev`.
3. Publish. Share that Google Sites URL publicly.

If the Cloudflare URL ever changes, you edit one embed block in the Google Site
and every existing link keeps working.

## 5. Manual events (Facebook-only, word-of-mouth) — later

1. Google Form with fields: Title, Date, Start time, End time, Venue, City,
   Category, Description, Link.
2. Form responses → Google Sheet → **File → Share → Publish to web** as CSV.
3. Add a `manual` adapter that reads that CSV URL and a registry entry for it.

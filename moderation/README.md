# Moderation

Both files here are **owner decisions**, written by the admin page
(`/admin`, through the Worker API in `worker/index.js`). CI only reads them
and never writes to them.

| file | written when you… | read by |
|------|-------------------|---------|
| `decisions.csv` | approve or reject a queued event | `verify.load_decisions`: approved events publish tagged "reviewed", and rejected ones are dropped for good |
| `annual_decisions.csv` | mark a suspected annual event as annual or not | `recurrence.load_annual_decisions`: overrides the automatic guess |

Columns: `key` (event fingerprint or annual-series key; don't edit), `decision`
(`approve`/`reject`, or `confirm`/`reject` for annual), `title`/`date`/`source`
(context only), `note`, `decided_at` (UTC). Clearing a decision on the page
removes the row.

Events wait in the queue when no trusted source vouches for them and no
second independent website confirms them, when they come from a potential
source, or when they were read out of page text. The reason shows on each
card. See "Verification" in `docs/ARCHITECTURE.md`.

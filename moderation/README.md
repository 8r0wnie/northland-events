# Moderation

`decisions.csv` is the admin's green-light / red-light record for events that
reach the review queue (a single low-trust source — currently the allevents.in
Facebook aggregator).

**To review:** open `https://<your-pages-domain>/review.html`, unlock with the
passphrase, and Approve / Reject / Skip each event (each links out to its source
so you can verify it yourself). Click **Export** and commit the file it gives you
back to this path.

**Columns:** `key` (stable event id — don't edit), `decision` (`approve` /
`reject` / blank = still pending), `title` / `date` / `source` (context only),
`note` (optional).

Approved events join the public calendar tagged "reviewed". Rejected events are
dropped permanently. Blank stays queued and off the calendar.

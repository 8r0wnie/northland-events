// Northland Events — admin API (Cloudflare Worker, free tier).
//
// Static files in site/ are served by the Workers assets binding exactly as
// before; this script only answers /api/*. Every write is a commit to the
// GitHub repo (the single source of truth), made with a fine-grained token
// stored as a Worker secret — so decisions are versioned, the daily scrape
// picks them up from the repo, and nothing depends on the owner's computer.
//
// Secrets (Cloudflare dashboard → Worker → Settings → Variables and Secrets):
//   ADMIN_PASSWORD  the admin passphrase (checked server-side)
//   GITHUB_TOKEN    fine-grained PAT, this repo only: Contents R/W, Actions R/W
// Vars (wrangler.toml): GITHUB_REPO, GITHUB_BRANCH
//
// Endpoints (all require header X-Admin-Key: <passphrase>):
//   POST /api/login                      -> {ok}
//   GET  /api/decisions                  -> review decisions (moderation/decisions.csv)
//   POST /api/decisions   {items:[...]}  -> upsert {key, decision, title, date, source, note}
//   GET  /api/annual                     -> annual-series decisions
//   POST /api/annual      {items:[...]}  -> upsert {key, decision: confirm|reject|"", title}
//   GET  /api/sources                    -> sources.yaml entries (fresh from the repo)
//   POST /api/sources     {changes:[{id, tier?, status?}]}
//   POST /api/sources/add {name, url, events_url?, area, state, category}
//   POST /api/rescrape                   -> dispatch the scrape workflow now
//   GET  /api/runs                       -> latest scrape workflow runs
//   POST /api/relay  (scraper only; auth = an in-progress Actions run, not the
//        passphrase) -> fetch one URL from Cloudflare for a `refresh: relay` host

const DECISIONS = "moderation/decisions.csv";
const ANNUAL = "moderation/annual_decisions.csv";
const SOURCES = "sources/sources.yaml";
const DECISION_COLS = ["key", "decision", "title", "date", "source", "note", "decided_at"];
const ANNUAL_COLS = ["key", "decision", "title", "decided_at"];
const TIERS = new Set(["trusted", "source", "potential"]);
const STATUSES = new Set(["active", "paused", "todo", "no-calendar", "blocked", "broken",
                          "dead", "duplicate", "closed"]);
const CATEGORIES = new Set(["gov", "chamber", "tourism", "venue", "news", "social", "aggregator"]);
const FIELD_ORDER = ["id", "name", "url", "events_url", "category", "state", "area", "platform",
                     "status", "tier", "autopromote", "trust", "refresh", "relay_hosts", "timefix", "notes"];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(request);
    try {
      return await api(request, env, url);
    } catch (err) {
      return json({ error: String(err && err.message || err) }, err && err.status || 500);
    }
  },
};

// ── routing ──────────────────────────────────────────────────────────────────
async function api(request, env, url) {
  // the scraper's relay authenticates differently (a live GitHub Actions run)
  // and uses that run's token, so it works without the admin secrets
  if (url.pathname === "/api/relay" && request.method === "POST") return relay(request, env);
  if (!env.ADMIN_PASSWORD || !env.GITHUB_TOKEN) {
    return json({ error: "admin API not configured (ADMIN_PASSWORD / GITHUB_TOKEN secrets)" }, 503);
  }
  if (!(await authorized(request, env))) {
    await new Promise((r) => setTimeout(r, 600));   // blunt password guessing a little
    return json({ error: "wrong passphrase" }, 401);
  }
  const route = `${request.method} ${url.pathname.replace(/\/+$/, "")}`;
  switch (route) {
    case "POST /api/login": return json({ ok: true });
    case "GET /api/decisions": return json({ items: await readCsv(env, DECISIONS) });
    case "POST /api/decisions": return json(await upsertCsv(env, DECISIONS, DECISION_COLS,
      await body(request), ["approve", "reject", ""], "review decision"));
    case "GET /api/annual": return json({ items: await readCsv(env, ANNUAL) });
    case "POST /api/annual": return json(await upsertCsv(env, ANNUAL, ANNUAL_COLS,
      await body(request), ["confirm", "reject", ""], "annual-event decision"));
    case "GET /api/sources": return json({ sources: parseSources((await getFile(env, SOURCES)).text) });
    case "POST /api/sources": return json(await changeSources(env, await body(request)));
    case "POST /api/sources/add": return json(await addSource(env, await body(request)));
    case "POST /api/rescrape": return json(await rescrape(env));
    case "GET /api/runs": return json(await runs(env));
    default: return json({ error: "not found" }, 404);
  }
}

// ── auth ─────────────────────────────────────────────────────────────────────
async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function authorized(request, env) {
  const given = request.headers.get("X-Admin-Key") || "";
  const [a, b] = await Promise.all([sha256(given), sha256(env.ADMIN_PASSWORD)]);
  let diff = 0;   // compare digests in constant time
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return given.length > 0 && diff === 0;
}

// ── GitHub contents API ──────────────────────────────────────────────────────
async function gh(env, path, init = {}) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "northland-events-admin",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  return res;
}

function b64decode(b64) {
  const bin = atob(b64.replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

function b64encode(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

async function getFile(env, path) {
  const res = await gh(env, `/contents/${path}?ref=${encodeURIComponent(env.GITHUB_BRANCH)}`);
  if (res.status === 404) return { text: "", sha: null };
  if (!res.ok) throw httpError(`GitHub read ${path}: ${res.status}`, 502);
  const data = await res.json();
  return { text: b64decode(data.content || ""), sha: data.sha };
}

// read -> mutate -> write, retrying if someone (CI) committed in between
async function updateFile(env, path, mutate, message) {
  for (let attempt = 0; attempt < 4; attempt++) {
    const { text, sha } = await getFile(env, path);
    const out = mutate(text);
    if (out === text) return { changed: false };
    const res = await gh(env, `/contents/${path}`, {
      method: "PUT",
      body: JSON.stringify({
        message: `[admin] ${message}`,
        content: b64encode(out),
        branch: env.GITHUB_BRANCH,
        ...(sha ? { sha } : {}),
      }),
    });
    if (res.ok) return { changed: true, commit: (await res.json()).commit?.sha };
    if (res.status !== 409 && res.status !== 422) {
      throw httpError(`GitHub write ${path}: ${res.status} ${await res.text()}`, 502);
    }
    await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
  }
  throw httpError(`GitHub write ${path}: kept conflicting, try again`, 409);
}

// ── CSV (decisions files) ────────────────────────────────────────────────────
function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') q = false;
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += c;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  const [head, ...rest] = rows.filter((r) => r.length > 1 || r[0]);
  if (!head) return [];
  return rest.map((r) => Object.fromEntries(head.map((h, i) => [h.trim(), r[i] ?? ""])));
}

function toCsv(cols, rows) {
  const esc = (v) => {
    const s = String(v ?? "");
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n") + "\n";
}

async function readCsv(env, path) {
  return parseCsv((await getFile(env, path)).text);
}

async function upsertCsv(env, path, cols, payload, allowed, label) {
  const items = Array.isArray(payload.items) ? payload.items : [];
  for (const it of items) {
    if (!it || typeof it.key !== "string" || !it.key || it.key.length > 300) throw httpError("bad key", 400);
    if (!allowed.includes(String(it.decision ?? ""))) throw httpError(`bad decision: ${it.decision}`, 400);
  }
  if (!items.length) return { changed: false, count: 0 };
  const now = new Date().toISOString().slice(0, 19) + "Z";
  const res = await updateFile(env, path, (text) => {
    const rows = parseCsv(text);
    const byKey = new Map(rows.map((r) => [r.key, r]));
    for (const it of items) {
      const row = byKey.get(it.key) || { key: it.key };
      for (const c of cols) if (c !== "key" && c !== "decided_at" && it[c] !== undefined) row[c] = String(it[c]).slice(0, 500);
      row.decided_at = now;
      if (!byKey.has(it.key)) { rows.push(row); byKey.set(it.key, row); }
    }
    // a cleared decision ("undo") drops the row entirely
    return toCsv(cols, rows.filter((r) => r.decision));
  }, `${items.length} ${label}${items.length === 1 ? "" : "s"}`);
  return { ...res, count: items.length };
}

// ── sources.yaml (one-line flow mappings; same format as scraper/srcedit.py) ──
const LINE_RE = /^([ \t]*- )\{id: ([A-Za-z0-9_-]+),[^\n]*\}[ \t]*$/gm;

function parseFlow(line) {
  // "{id: x, name: "a, b", tier: potential}" -> [[k, v], ...] keeping order
  const s = line.trim().replace(/^-\s*/, "");
  const out = [];
  let i = s.indexOf("{") + 1;
  while (i < s.length) {
    while (s[i] === " " || s[i] === ",") i++;
    if (s[i] === "}") break;
    const colon = s.indexOf(":", i);
    const key = s.slice(i, colon).trim();
    i = colon + 1;
    while (s[i] === " ") i++;
    let val;
    if (s[i] === '"') {
      let j = i + 1, raw = '"';
      while (j < s.length) {
        if (s[j] === "\\") { raw += s[j] + s[j + 1]; j += 2; continue; }
        raw += s[j];
        if (s[j] === '"') break;
        j++;
      }
      val = JSON.parse(raw);
      i = j + 1;
    } else {
      let j = i;
      while (j < s.length && s[j] !== "," && s[j] !== "}") j++;
      val = s.slice(i, j).trim();
      i = j;
    }
    out.push([key, val]);
  }
  return out;
}

function formatFlow(indent, pairs) {
  const map = new Map(pairs);
  const keys = [...FIELD_ORDER.filter((k) => map.has(k)), ...[...map.keys()].filter((k) => !FIELD_ORDER.includes(k))];
  const parts = keys.map((k) => {
    const v = String(map.get(k) ?? "");
    const bare = ["id", "category", "state", "status"].includes(k) && /^[A-Za-z0-9_-]+$/.test(v);
    return `${k}: ${bare ? v : JSON.stringify(v)}`;
  });
  return `${indent}{${parts.join(", ")}}`;
}

function parseSources(text) {
  const out = [];
  for (const m of text.matchAll(LINE_RE)) out.push(Object.fromEntries(parseFlow(m[0])));
  return out;
}

function setFields(text, id, fields) {
  const re = new RegExp(`^([ \\t]*- )\\{id: ${id.replace(/[-]/g, "\\-")},[^\\n]*\\}[ \\t]*$`, "m");
  const m = text.match(re);
  if (!m) throw httpError(`source ${id} not found`, 404);
  const pairs = parseFlow(m[0]);
  const map = new Map(pairs);
  for (const [k, v] of Object.entries(fields)) map.set(k, v);
  return text.replace(re, formatFlow(m[1], [...map.entries()]));
}

async function changeSources(env, payload) {
  const changes = Array.isArray(payload.changes) ? payload.changes : [];
  if (!changes.length) return { changed: false, count: 0 };
  for (const c of changes) {
    if (!c || !/^[A-Za-z0-9_-]+$/.test(c.id || "")) throw httpError("bad source id", 400);
    if (c.tier !== undefined && !TIERS.has(c.tier)) throw httpError(`bad tier: ${c.tier}`, 400);
    if (c.status !== undefined && !STATUSES.has(c.status)) throw httpError(`bad status: ${c.status}`, 400);
  }
  const res = await updateFile(env, SOURCES, (text) => {
    let out = text;
    for (const c of changes) {
      const f = {};
      // a manual tier choice is final: switch off auto-promotion for it
      if (c.tier !== undefined) { f.tier = c.tier; f.autopromote = ""; }
      if (c.status !== undefined) f.status = c.status;
      out = setFields(out, c.id, f);
    }
    return out;
  }, `${changes.length} source change${changes.length === 1 ? "" : "s"}`);
  return { ...res, count: changes.length };
}

function slug(text) {
  return String(text).toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "source";
}

async function addSource(env, p) {
  const name = String(p.name || "").trim().slice(0, 120);
  const url = String(p.url || "").trim();
  const eventsUrl = String(p.events_url || "").trim();
  const area = String(p.area || "").trim().slice(0, 60);
  const state = String(p.state || "MN").toUpperCase();
  const category = String(p.category || "venue");
  if (!name) throw httpError("name required", 400);
  for (const u of [url, eventsUrl].filter(Boolean)) {
    let parsed;
    try { parsed = new URL(u); } catch { throw httpError(`not a URL: ${u}`, 400); }
    if (!/^https?:$/.test(parsed.protocol)) throw httpError(`not http(s): ${u}`, 400);
  }
  if (!url) throw httpError("url required", 400);
  if (!["MN", "WI", "MI"].includes(state)) throw httpError("state must be MN, WI or MI", 400);
  if (!CATEGORIES.has(category)) throw httpError(`bad category: ${category}`, 400);
  let id = "";
  const res = await updateFile(env, SOURCES, (text) => {
    const existing = new Set(parseSources(text).map((s) => s.id));
    const sameUrl = parseSources(text).find((s) => (s.url || "").replace(/\/+$/, "") === url.replace(/\/+$/, ""));
    if (sameUrl) throw httpError(`already in the list as ${sameUrl.id}`, 409);
    const base = slug(name);
    id = base;
    for (let n = 2; existing.has(id); n++) id = `${base}-${n}`;
    const today = new Date().toISOString().slice(0, 10);
    const line = formatFlow("  - ", [
      ["id", id], ["name", name], ["url", url], ["events_url", eventsUrl], ["category", category],
      ["state", state], ["area", area], ["platform", ""], ["status", "active"],
      ["tier", "potential"], ["autopromote", "yes"],
      ["notes", `Added from the admin page ${today}. Potential source: events go to review; auto-promotes to source after events on 5 of 7 runs.`],
    ]);
    const header = "  # ─────────────────────────── Added from the admin page ───────────────────────────";
    let out = text.replace(/\s*$/, "\n");
    if (!out.includes(header)) out += `\n${header}\n`;
    return out + line + "\n";
  }, `add potential source "${name}"`);
  return { ...res, id };
}

// ── workflow runs ────────────────────────────────────────────────────────────
async function rescrape(env) {
  const res = await gh(env, `/actions/workflows/scrape.yml/dispatches`, {
    method: "POST", body: JSON.stringify({ ref: env.GITHUB_BRANCH }),
  });
  if (res.status !== 204) throw httpError(`could not start the scrape: ${res.status} ${await res.text()}`, 502);
  return { started: true };
}

async function runs(env) {
  const res = await gh(env, `/actions/workflows/scrape.yml/runs?per_page=3&branch=${encodeURIComponent(env.GITHUB_BRANCH)}`);
  if (!res.ok) throw httpError(`GitHub runs: ${res.status}`, 502);
  const data = await res.json();
  return {
    runs: (data.workflow_runs || []).map((r) => ({
      status: r.status, conclusion: r.conclusion, event: r.event,
      created_at: r.created_at, updated_at: r.updated_at, url: r.html_url,
    })),
  };
}

// ── relay for `refresh: relay` sources ───────────────────────────────────────
// Owner decision 2026-10-12 (CLAUDE.md Rule 1): a few sites refuse GitHub's
// datacenter IPs. For those sources only, the daily scrape sends its requests
// here and this Worker makes them from Cloudflare instead. One fixed origin,
// the scraper's own 2 s per-domain delay, no rotation, no challenge solving —
// and only for hosts listed by `refresh: relay` entries in sources.yaml.
//
// Auth: the caller must present the GitHub Actions token of a run of THIS repo
// that is still in progress (X-Relay-Run + X-Relay-Token); we confirm it with
// GitHub. No extra secret to manage, and it is useless once the run ends.
const verifiedRuns = new Map();   // run id -> expiry (per isolate; best effort)
let relayHostsCache = { at: 0, hosts: new Set() };

async function relayHosts(env, token) {
  if (Date.now() - relayHostsCache.at < 10 * 60 * 1000) return relayHostsCache.hosts;
  const hosts = new Set();
  const { text } = await getFile({ ...env, GITHUB_TOKEN: token || env.GITHUB_TOKEN }, SOURCES);
  for (const s of parseSources(text)) {
    if (s.refresh !== "relay") continue;
    for (const u of [s.url, s.events_url]) {
      try { hosts.add(new URL(u).hostname.toLowerCase()); } catch {}
    }
    for (const h of String(s.relay_hosts || "").split(",")) if (h.trim()) hosts.add(h.trim().toLowerCase());
  }
  relayHostsCache = { at: Date.now(), hosts };
  return hosts;
}

async function verifyRun(env, runId, token) {
  if (!/^\d{1,20}$/.test(runId || "") || !token) return false;
  const until = verifiedRuns.get(runId);
  if (until && until > Date.now()) return true;
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/runs/${runId}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json",
               "User-Agent": "northland-events-relay" },
  });
  if (!res.ok) return false;
  const run = await res.json();
  if (run.status === "completed") return false;
  verifiedRuns.set(runId, Date.now() + 15 * 60 * 1000);
  return true;
}

async function relay(request, env) {
  const ok = env.RELAY_DEV === "1"   // local `wrangler dev` testing only (.dev.vars)
    || await verifyRun(env, request.headers.get("X-Relay-Run"), request.headers.get("X-Relay-Token"));
  if (!ok) return json({ error: "relay: not an in-progress run of this repo" }, 401);
  const p = await body(request);
  let target;
  try { target = new URL(p.url); } catch { return json({ error: "relay: bad url" }, 400); }
  if (!/^https?:$/.test(target.protocol)) return json({ error: "relay: http(s) only" }, 400);
  const hosts = await relayHosts(env, request.headers.get("X-Relay-Token"));
  if (!hosts.has(target.hostname.toLowerCase())) {
    return json({ error: `relay: ${target.hostname} is not a refresh: relay host` }, 403);
  }
  const method = (p.method || "GET").toUpperCase();
  if (!["GET", "POST", "HEAD"].includes(method)) return json({ error: "relay: bad method" }, 400);
  const headers = {};
  const DROP = /^(host|content-length|cookie|authorization|connection|accept-encoding|transfer-encoding|upgrade|keep-alive|te|x-relay-.*|cf-.*|proxy-.*|sec-.*)$/i;
  for (const [k, v] of Object.entries(p.headers || {})) {
    if (!DROP.test(k)) headers[k] = String(v);   // never credentials or hop-by-hop headers
  }
  const res = await fetch(target.toString(), {
    method, headers, redirect: "follow",
    body: method === "POST" && p.body_b64 ? Uint8Array.from(atob(p.body_b64), (c) => c.charCodeAt(0)) : undefined,
  });
  const buf = new Uint8Array(await res.arrayBuffer());
  if (buf.length > 12 * 1024 * 1024) return json({ error: "relay: response too large" }, 502);
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return json({
    status: res.status, url: res.url,
    headers: { "content-type": res.headers.get("content-type") || "" },
    body_b64: btoa(bin),
  });
}

// ── helpers ──────────────────────────────────────────────────────────────────
async function body(request) {
  try { return await request.json(); } catch { throw httpError("expected a JSON body", 400); }
}

function httpError(message, status) {
  const e = new Error(message);
  e.status = status;
  return e;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store",
               "X-Robots-Tag": "noindex" },
  });
}

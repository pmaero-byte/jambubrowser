# Web-data API (v1)

A local, MIT-licensed web-data layer for agents: scrape, map, crawl, batch,
extract and change tracking. It fetches pages, turns them into LLM-ready text,
and — unlike a hosted scraping API — returns **a verifiable receipt for every
page** and runs untrusted content through a quarantine boundary before any model
sees it.

Base: the engine's own host (default `http://127.0.0.1:8001`).

## Auth

Every route is behind an API key (`POST /api-keys/create`), sent as
`Authorization: Bearer jambu_…` or `X-API-Key: …`.

Policy via `JAMBU_WEBDATA_AUTH`:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | Key required from **non-loopback** peers only — local dev stays frictionless, an exposed port does not. |
| `required` | Always. Use behind a proxy or tunnel, where every request looks local. |
| `off` | Never. Explicit opt-out. |

An unrecognised value falls back to `auto` (fail safe).

## Rendering

Every fetch route takes `render`:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | HTTP first; escalate to the headless browser **only if the response looks like a JavaScript shell** (large document, almost no readable text, or an empty SPA mount point). |
| `always` | Always render with the browser. |
| `never` | HTTP only. |

`auto` exists because a browser launch per page is expensive and most pages are
static. If Playwright is not installed, the browser path degrades to HTTP and
says so in `note` rather than failing the request.

## Caching

Fetch routes take `max_age` (seconds, default **0** — never serve stale data).
With `max_age > 0` a fresh cached copy is served, which is what makes repeated
crawls of the same site cheap. Only `200` responses are cached; a cache failure
never fails the fetch. `GET /v1/cache/stats`, `DELETE /v1/cache`.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/scrape` | One URL → `markdown` / `html` / `links` + receipt + `transport`. |
| `POST /v1/map` | Discover a site's URLs (sitemaps + the seed's links). |
| `POST /v1/crawl` | Bounded crawl. Returns a **job id**; `GET /v1/crawl/{id}` polls, `DELETE` cancels, `GET /v1/crawl` lists. `sync=true` blocks for small jobs. |
| `POST /v1/batch` | Fixed URL list, concurrent. Same job shape as crawl. |
| `POST /v1/extract` | Caller-supplied field schema → **typed** JSON, via the quarantine boundary. |
| `POST /v1/changes/check` | Has this URL changed since last check? Returns hash + bounded diff. |
| `GET /v1/changes` | Watched URLs, or the change log for one URL. |
| `DELETE /v1/changes` | Stop tracking a URL. |

### Crawl and batch jobs

`JAMBU_CRAWL_CONCURRENCY` (default 3) caps concurrent jobs;
`JAMBU_BATCH_CONCURRENCY` (default 5) caps per-batch URL concurrency;
`JAMBU_CRAWL_RESULT_TTL` (default 3600s) is how long finished results stay
pollable. Pages stream into a job as they land, so a poller sees partial
results. Cancellation is cooperative (checked between pages).

**Durability.** Job records are written to SQLite on every state change, and
results are written once at completion (stored page text is capped, since the
full text was already returned to you). A poll therefore still answers after a
restart — `GET /v1/crawl/{id}` and `GET /v1/batch/{id}` fall back to the store
when the in-memory record is gone.

A job the process died inside is marked **`interrupted`** at the next startup
rather than left at `running`, so a poller is never left waiting on work that
will never advance. `interrupted` is terminal.

Job ids are not interchangeable: a crawl id read through the batch endpoint
returns 404, and vice versa.

### Crawling behaviour

Robots.txt is **respected by default** (`respect_robots`), including `*` and `$`
pattern rules per RFC 9309. Crawls are bounded by `max_depth`, `page_limit`,
`include_patterns`/`exclude_patterns` and `same_domain`.

### Extraction

`/v1/extract` takes fields, not prose:

```jsonc
{
  "url": "https://example.com/product",
  "fields": [
    { "name": "item_name",   "type": "str" },                      // content
    { "name": "price",       "type": "float", "role": "control" },
    { "name": "availability","type": "enum",  "role": "control",
      "choices": ["in_stock", "out_of_stock", "unknown"] }
  ]
}
```

A `role: "control"` field — one that may drive an action — **cannot be a free
string**; that is rejected with a 400. Free text must be `role: "content"`
(default), and is never accepted as a tool argument. Undeclared fields in the
model's output are rejected. Design rules and their sources:
`docs/QUARANTINE_SCHEMA_DESIGN.md`.

## Fetch receipts

Every page carries:

```jsonc
{
  "url": "...", "fetched_at": 0, "status": 200,
  "content_sha256": "…",   // hash of the raw bytes fetched
  "bytes_in": 0, "bytes_out": 0,
  "removed_invisible": 0,  // zero-width / bidi characters stripped
  "injection_markers": []  // labels of injection-like patterns found
}
```

The hash is the point: it lets a third party verify that the text they were
shown is the text that was fetched. No hosted scraping API ships this.

## Not implemented

- **PDF parsing** — a `.pdf` URL is not decoded.
- **Anti-bot / CAPTCHA handling** — this is a plain client plus a local browser;
  it does not proxy or evade. Protected sites will fail, and the receipt will
  say so.
- **Resuming a partially-completed crawl** — jobs are durable for *status and
  results*, but an interrupted crawl is not resumed; it is reported as
  interrupted and must be re-submitted.

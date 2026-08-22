# Architecture

```
                    ┌──────────────┐
                    │    Vercel    │
                    │  Next.js App │  ← apps/web (SSR/ISR, admin UI, public API routes)
                    └──────┬───────┘
                           │ anon key (RLS-enforced)
                           ▼
                    ┌──────────────┐
                    │   Supabase   │
                    │  PostgreSQL  │  ← supabase/migrations (schema, RLS, triggers)
                    │  Auth        │
                    │  Storage     │
                    └──────▲───────┘
                           │ service-role key (bypasses RLS — server only)
                    ┌──────┴───────┐
                    │    Render    │
                    │ Python       │  ← apps/ingestion (FastAPI + APScheduler)
                    │ Ingestion    │
                    │ Scheduler    │
                    └──────┬───────┘
                           │ only permitted RSS/Atom/API endpoints,
                           │ robots.txt-checked, rate-limited
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
        Source A       Source B       Source C
     (admin-configured, allowed=true only after manual ToS/robots.txt review)
```

## Why this split

- **Vercel** never talks to sources directly and never holds the service-role
  key — it only ever uses the anon key, so Row Level Security is the real
  authorization boundary for every browser-facing request.
- **Render** runs the only process that holds `SUPABASE_SERVICE_ROLE_KEY` and
  is the only process that fetches external sources — one place to audit for
  scraping-politeness (robots.txt, rate limits, backoff) and one place to
  rotate a leaked secret.
- **Supabase** owns auth, the relational schema, and RLS policies, so
  authorization rules live in one place (the database) rather than being
  re-implemented per API route.

## Ingestion source of truth

Sources are **rows in the `sources` table**, not code. Adding a new outlet
is an admin-panel operation (Section 47 of the original spec), not a
deploy. The only adapter shipped by default is `generic_rss`
(`apps/ingestion/app/sources/generic_rss.py`), which works against any
RSS/Atom feed. A `sources` row stays `allowed = false` — and is therefore
never fetched — until an administrator has manually verified robots.txt
and that outlet's Terms of Service permit this kind of automated access,
and has supplied a real feed/API URL. This is why Ekantipur / 24 News /
Online Taja Khabar are **not** hard-coded as adapters in this scaffold —
each needs that manual verification step first (see spec section 33/34).

## Editorial workflow

`ingested → pending_review → editor_review → approved → published`
(`article_status` enum, `0001_init_schema.sql`). Nothing the ingestion
worker writes is publicly visible until it clears this pipeline — the
public RLS policy on `articles` only allows `select` where
`status = 'published'`.

## What's real vs. what's a stub in this scaffold

| Piece | Status |
|---|---|
| DB schema, RLS, triggers, search vector, trending view | Complete, runnable migrations |
| Ingestion pipeline (fetch→dedupe→categorize→store→log) | Complete for `generic_rss` sources |
| Duplicate detection (hash, canonical URL, fuzzy title) | Complete |
| Scheduler with per-source interval + exponential backoff | Complete |
| Categorizer | Keyword-based stub — swap for AI classification later (spec §41) without touching the pipeline |
| Frontend: homepage, article page, health check, admin sync trigger | Complete, real Supabase queries, no mock data |
| Frontend: category pages, search, comments UI, bookmarks UI, admin CMS UI, auth pages, PWA manifest, notifications | **Not yet built** — see README "Next stages" |
| Tests | One real pytest module for duplicate detection; no Playwright/e2e yet |
| CI | Lint + typecheck + build + pytest; no security-scanning step yet |

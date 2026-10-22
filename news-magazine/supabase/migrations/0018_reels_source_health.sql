-- =============================================================
-- Reels source health
--
-- One row per reels provider, saying whether it is running and why
-- not when it is not.
--
-- WHY THIS EXISTS
-- ---------------
-- The Reels tab looked broken and the reason was invisible. The
-- worker has four possible sources - Wikimedia, Pexels, Pixabay and
-- YouTube - and three of them need a key that is not set. A provider
-- with no key is SKIPPED, which the code deliberately logs as INFO
-- rather than WARNING, because "nothing is misconfigured, we simply
-- have no credential for this provider" is not an error.
--
-- That is the right behaviour for a log and the wrong behaviour for an
-- operator. From outside the process there was no way to tell the
-- difference between "the provider is working and found nothing" and
-- "the provider was never switched on". So the state is written where
-- the dashboard can read it.
--
-- The row that matters is the one with `enabled = false`, because it
-- carries `missing_env_var` - the exact name to set. That is the
-- difference between a diagnosis and a shrug.
-- =============================================================

create table if not exists reels_source_health (
  source_key text primary key,
  -- False when the provider was skipped for want of a credential.
  enabled boolean not null default false,
  -- The environment variable that switches this provider on, when it
  -- is off. Null for a keyless provider, which is always on.
  missing_env_var text,
  -- How many keys the provider has configured. Never the keys.
  key_count integer not null default 0,
  -- The provider's own minimum interval between polls, in seconds.
  -- YouTube's is 3600 because its quota is priced per search.
  min_poll_seconds integer not null default 0,
  -- The last pass that actually ran the provider.
  last_run_at timestamptz,
  -- What the last pass produced.
  last_count integer,
  last_error text,
  -- Whether the last pass succeeded. Null when it never ran.
  last_status text,
  updated_at timestamptz not null default now()
);

alter table reels_source_health enable row level security;

-- Readable by the admin dashboard. There is nothing secret in this
-- table: it says which provider is on and which variable is missing,
-- never a key.
create policy "reels_source_health_public_select" on reels_source_health
  for select using (true);

create policy "reels_source_health_service_write" on reels_source_health
  for all using (false) with check (false);

-- =============================================================
-- Admin sign-in: durable brute-force counters
--
-- WHY THIS EXISTS
-- ---------------
-- The sign-in route counts failed attempts so that guessing a
-- password is slow. Until now that count lived in a Map inside the
-- route handler, which has three problems:
--
--   1. It is gone when the process restarts, and a Vercel function
--      is a different process on a cold start. A deploy - or an
--      attacker who can make the function go cold - wipes the slate.
--   2. It counts per ADDRESS only. An attacker with a botnet walks
--      around the limit by changing address, and the account itself
--      is never limited at all.
--   3. It is unbounded, so a stream of attempts from new addresses is
--      also a slow memory leak.
--
-- This table moves the counters into the database, where they
-- survive a restart, and adds the missing per-account half.
--
-- THE ACCOUNT COUNTER IS DELAYED, NOT LOCKED
-- ------------------------------------------
-- The account counter slows a guesser down; it never locks the
-- account. A hard lockout is a denial of service waiting to happen:
-- the seeded operator's email address is published in this
-- repository, so anybody could lock the operator out by failing ten
-- times in a row and doing it again every fifteen minutes. With a
-- delay instead, the operator can always sign in, and a guesser gets
-- one attempt every eight seconds at worst.
--
-- The counter is keyed on the address that was TYPED, not on the
-- accounts that exist, so an address nobody owns is counted exactly
-- like one that does. That is what keeps the delay from being an
-- oracle: "is there an account with this email?" has the same answer
-- for every address, and takes the same time to give it.
--
-- The IP counter is a hard block, because blocking an address does
-- not lock out a person.
-- =============================================================

create table if not exists admin_login_attempts (
  -- 'account' for the email address that was typed, 'ip' for the
  -- address the request came from.
  scope text not null check (scope in ('account', 'ip')),
  -- The email address, or the IP address. Both are stored as they
  -- arrive, lowercased for the email.
  subject text not null,
  -- How many failures have happened inside the current window.
  failures integer not null default 0,
  -- When the window opened. The window is 15 minutes and it starts
  -- again when it expires.
  first_attempt_at timestamptz not null default now(),
  last_attempt_at timestamptz not null default now(),
  primary key (scope, subject)
);

-- The sweeper that removes counters nobody is using any more.
create index if not exists admin_login_attempts_last_attempt_at_idx
  on admin_login_attempts (last_attempt_at);

-- ---------------------------------------------------------
-- Row level security
--
-- The same rule as the admin tables: no SELECT policy at all, so
-- the anon key reads nothing, and the web app reads and writes this
-- table with the service role key, which bypasses RLS.
--
-- A table of failed attempts is a list of what somebody tried, and
-- "what somebody tried" against an admin panel is worth keeping to
-- the people who run the panel.
-- ---------------------------------------------------------

alter table admin_login_attempts enable row level security;

drop policy if exists "admin_login_attempts_service_write" on admin_login_attempts;

create policy "admin_login_attempts_service_write" on admin_login_attempts
  for all using (false) with check (false);

-- ---------------------------------------------------------
-- Counting a failure
--
-- The increment happens in the database so that two requests that
-- arrive at the same moment cannot both read "3 failures" and both
-- write "4". The window is opened by the first failure and expires
-- fifteen minutes after it.
--
-- It returns the new count, which is what the caller needs to know
-- how long to wait before answering.
-- ---------------------------------------------------------

create or replace function admin_login_failure(
  p_scope text,
  p_subject text
) returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  current_count integer;
  window_start timestamptz;
  next_count integer;
begin
  select failures, first_attempt_at
    into current_count, window_start
    from admin_login_attempts
   where scope = p_scope and subject = p_subject
   for update;

  if current_count is null or now() - window_start > interval '15 minutes' then
    next_count := 1;
    window_start := now();
  else
    next_count := current_count + 1;
  end if;

  insert into admin_login_attempts (scope, subject, failures, first_attempt_at, last_attempt_at)
  values (p_scope, p_subject, next_count, window_start, now())
  on conflict (scope, subject) do update
    set failures = excluded.failures,
        first_attempt_at = excluded.first_attempt_at,
        last_attempt_at = excluded.last_attempt_at;

  return next_count;
end;
$$;

-- Only the service role may count a failure. `public` covers every
-- other role, and a function that any role may call is a function
-- any role may use to write to a table it otherwise cannot touch.
revoke all on function admin_login_failure(text, text) from public;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function admin_login_failure(text, text) to service_role;
  end if;
end $$;

-- ---------------------------------------------------------
-- The hash version
--
-- `password_hash_version` says which hashing policy the stored hash
-- was written under. Version 1 is the seeded hash, whose salt is
-- published in this repository. Version 2 is a hash with a salt
-- generated for that one account.
--
-- The login route rewrites a version 1 hash the first time its
-- account signs in, so every account ends up with a salt of its own
-- without anybody having to reset a password.
--
-- `password_updated_at` records when the hash last changed, so the
-- operator can see how old a credential is.
-- ---------------------------------------------------------

alter table admin_users
  add column if not exists password_hash_version integer not null default 1;

alter table admin_users
  add column if not exists password_updated_at timestamptz;

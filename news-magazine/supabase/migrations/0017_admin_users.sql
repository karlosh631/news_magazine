-- =============================================================
-- Admin sign-in
--
-- A self-contained admin login that does not need the operator to
-- touch Supabase at all.
--
-- WHY THIS EXISTS ALONGSIDE THE SUPABASE AUTH PATH
-- -------------------------------------------------
-- `apps/web/middleware.ts` already gates /admin on a Supabase Auth
-- session plus a `user_roles` row, and that path is untouched. But
-- creating a Supabase user and a role row is four clicks in a
-- dashboard, and the brief for this site is that it works with nobody
-- touching it. So there is a second way in: a table of admins with a
-- hashed password, and a signed session cookie.
--
-- The two paths are equal. Either one opens the door.
--
-- WHY THE PASSWORD IS NOT IN THIS FILE
-- -----------------------------------
-- What IS in this file is a PBKDF2-SHA256 hash: a random 16-byte
-- salt, 210,000 iterations, and the resulting 32 bytes. The
-- plaintext password is not here, and is not anywhere in the
-- repository. `apps/web/lib/admin-auth.ts` verifies a login against
-- this hash and nothing else has to know the password.
--
-- The salt is fixed, which is what a seeded credential is: a fresh
-- salt is generated on the first password change and the row is
-- updated with it. A per-user random salt for a single seeded
-- account buys nothing and would make the seed non-deterministic.
--
-- PBKDF2 rather than bcrypt because the web app has no native crypto
-- dependency: Web Crypto has PBKDF2 built in, and Web Crypto is
-- available in BOTH the Node runtime the route handlers run in and
-- the Edge runtime the middleware runs in, so one implementation
-- serves both.
-- =============================================================

create table if not exists admin_users (
  id uuid primary key default uuid_generate_v4(),
  email text not null unique,
  -- pbkdf2-sha256$<iterations>$<salt base64>$<hash base64>
  password_hash text not null,
  display_name text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  last_login_at timestamptz
);

-- A session row per signed-in browser, so a session can be revoked.
--
-- The cookie carries the session id and an HMAC over it; the id is
-- only useful to this table, and deleting the row ends the session
-- immediately without waiting for the cookie to expire. That is the
-- difference between "sign out" and "sign out everywhere".
create table if not exists admin_sessions (
  id uuid primary key default uuid_generate_v4(),
  admin_user_id uuid not null references admin_users (id) on delete cascade,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  user_agent text
);

create index if not exists admin_sessions_admin_user_id_idx
  on admin_sessions (admin_user_id);
create index if not exists admin_sessions_expires_at_idx
  on admin_sessions (expires_at);

-- ---------------------------------------------------------
-- Row level security
--
-- Neither table is readable by the anonymous key. The web app reads
-- them with the SERVICE ROLE key, which bypasses RLS, and the login
-- route is the only thing that ever touches the password hashes.
--
-- This matters more here than anywhere else in the schema: a policy
-- of `using (true)` on this table would publish every admin's
-- password hash to anyone who asks, and a hash is a credential.
-- ---------------------------------------------------------

alter table admin_users enable row level security;
alter table admin_sessions enable row level security;

-- No SELECT policy at all: the anon key reads nothing.

create policy "admin_users_service_write" on admin_users
  for all using (false) with check (false);

create policy "admin_sessions_service_write" on admin_sessions
  for all using (false) with check (false);

-- ---------------------------------------------------------
-- The seeded administrator
--
-- thapakarlosh631@gmail.com
--
-- The hash below is PBKDF2-SHA256, 210000 iterations, salt
-- 9eAfn0QvNAWZhY8Q+dtMiQ==. It was generated from the operator's
-- password and the plaintext is not stored anywhere in this
-- repository.
--
-- `on conflict (email) do nothing` so re-running the migration never
-- RESETS a password the operator has since changed. A migration that
-- silently undid a password change every time it was applied would be
-- a trap.
-- ---------------------------------------------------------

insert into admin_users (email, password_hash, display_name)
values (
  'thapakarlosh631@gmail.com',
  'pbkdf2-sha256$210000$9eAfn0QvNAWZhY8Q+dtMiQ==$a2ZYetjYH2HHf35j749aVfGQpGOqEkjfUGBn/vseolw=',
  'Karlosh Thapa'
)
on conflict (email) do nothing;

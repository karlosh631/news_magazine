-- =============================================================
-- Push subscriptions
--
-- WHAT THIS IS
-- ------------
-- A browser that is going to be told about the news while it is
-- closed has to be reachable: the server needs the address the push
-- service will deliver to. That address is a subscription, and this
-- table is where it is kept.
--
-- It is one row per device, not per reader: a reader with a phone and
-- a laptop has two subscriptions, and the same person on two tabs of
-- the same browser has one (the endpoint is the same).
--
-- WHY IT IS SMALL
-- ----------------
-- Three strings and two timestamps. The endpoint is the push
-- service's address for that browser, the two keys are what the
-- server signs its messages with, and the timestamps are for the
-- pruning: a subscription that the push service has stopped
-- accepting is a row that is taking up space for nothing.
--
-- THE PRUNING
-- -----------
-- A push service answers 404 or 410 for a subscription it has
-- dropped - the reader uninstalled the site, or the browser was
-- reset - and the sender deletes the row when it sees one. The
-- `last_seen_at` column is the other half: a subscription that has
-- not been seen for a month is a subscription from a device that is
-- no longer there, and the sender prunes those too.
--
-- The table is not read by the public site. Only the service role
-- key touches it, and only from the route that sends the push.
-- =============================================================

create table if not exists push_subscriptions (
  id uuid primary key default uuid_generate_v4(),

  -- The push service's address for that browser. It is the natural
  -- key: the same browser always has the same endpoint, and a
  -- re-subscribe replaces the old row rather than adding one.
  endpoint text not null unique,

  -- The two keys the server signs its messages with.
  p256dh text not null,
  auth text not null,

  -- What the device is, for the operator's list.
  user_agent text,

  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

-- The sender reads the whole table; the prune reads it by age.
create index if not exists push_subscriptions_last_seen_idx
  on push_subscriptions (last_seen_at);

-- =============================================================
-- Row level security
--
-- The table is server-only: the service role key reads and writes
-- it, and the anonymous key — which is what the public site has —
-- cannot. A subscription is a way of reaching a person's device, and
-- nothing in the public site needs to know who has one.
-- =============================================================

alter table push_subscriptions enable row level security;

revoke all on push_subscriptions from anon, authenticated;

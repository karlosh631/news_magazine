-- =============================================================
-- 0026_analytics_events_write_policy.sql
-- Let the reader-facing key write reader events again.
--
-- WHY THIS EXISTS
-- ---------------
-- `recordEvent()` in apps/web/lib/analytics-events.ts inserts into
-- `analytics_events` with the ANON key, and the only thing that
-- permits that insert is the policy
--
--   create policy "analytics_events_insert_any" on analytics_events
--     for insert with check (true);
--
-- from 0002_rls_policies.sql. The dashboard READS the table with the
-- service-role key, so the read side was fine - the WRITE side was
-- broken, and the failure was invisible: the error only reached the
-- Vercel function log, and the panel said "No reader events recorded
-- yet" for ever.
--
-- 0002 uses a bare `create policy`, with no `drop policy if exists` in
-- front of it, so the file cannot be re-applied. The operator's
-- migration runner stops at the first error and does NOT run the rest
-- of the file (see the note in 0017_admin_users.sql), which means a
-- run that stopped anywhere above the analytics_events policies left
-- the insert policy uncreated - and every write to this table has
-- been rejected since.
--
-- The code now writes with the service-role key, which bypasses both
-- RLS and grants, so it lands whatever state the policy is in. This
-- migration corrects the schema anyway, so that the anon path works
-- as the code documents it and so the policy and the grant are not
-- silently lost again.
--
-- Idempotent: the policies are dropped first and recreated, and the
-- grant is re-asserted. If the policy and the grant are already
-- correct, this changes nothing.
-- =============================================================

alter table analytics_events enable row level security;

drop policy if exists "analytics_events_insert_any" on analytics_events;
create policy "analytics_events_insert_any" on analytics_events
  for insert with check (true);

drop policy if exists "analytics_events_staff_select" on analytics_events;
create policy "analytics_events_staff_select" on analytics_events
  for select using (has_role(array['admin','super_admin','editor']::app_role[]));

-- The privileges the anon and authenticated keys need in order to
-- write here. The service role already bypasses RLS and grants.
grant insert on table analytics_events to anon, authenticated;

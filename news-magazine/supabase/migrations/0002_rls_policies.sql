-- =========================================================
-- 0002_rls_policies.sql
-- Row Level Security. Default posture: deny, then allow narrowly.
-- Roles: super_admin, admin, editor, journalist, moderator,
--        advertiser, registered_user. Anonymous = no JWT.
-- =========================================================

-- Helper already defined in 0001: has_role(app_role[])

alter table profiles enable row level security;
alter table user_roles enable row level security;
alter table sources enable row level security;
alter table source_sync_logs enable row level security;
alter table source_errors enable row level security;
alter table categories enable row level security;
alter table tags enable row level security;
alter table authors enable row level security;
alter table articles enable row level security;
alter table article_categories enable row level security;
alter table article_tags enable row level security;
alter table article_authors enable row level security;
alter table article_sources enable row level security;
alter table media enable row level security;
alter table article_media enable row level security;
alter table comments enable row level security;
alter table comment_reports enable row level security;
alter table bookmarks enable row level security;
alter table reading_history enable row level security;
alter table likes enable row level security;
alter table notifications enable row level security;
alter table subscriptions enable row level security;
alter table newsletter_subscribers enable row level security;
alter table advertisement_campaigns enable row level security;
alter table advertisements enable row level security;
alter table settings enable row level security;
alter table pages enable row level security;
alter table menus enable row level security;
alter table seo_metadata enable row level security;
alter table analytics_events enable row level security;
alter table article_views enable row level security;
alter table audit_logs enable row level security;

-- ---------------------------------------------------------
-- profiles: user manages own; admins manage all
-- ---------------------------------------------------------
create policy "profiles_select_own_or_admin" on profiles
  for select using (id = auth.uid() or has_role(array['admin','super_admin']::app_role[]));
create policy "profiles_update_own" on profiles
  for update using (id = auth.uid()) with check (id = auth.uid());
create policy "profiles_insert_own" on profiles
  for insert with check (id = auth.uid());
create policy "profiles_admin_all" on profiles
  for all using (has_role(array['super_admin']::app_role[]));

-- ---------------------------------------------------------
-- user_roles: only super_admin/admin manage; user can read own
-- ---------------------------------------------------------
create policy "user_roles_select_own_or_admin" on user_roles
  for select using (user_id = auth.uid() or has_role(array['admin','super_admin']::app_role[]));
create policy "user_roles_admin_write" on user_roles
  for all using (has_role(array['super_admin']::app_role[]))
  with check (has_role(array['super_admin']::app_role[]));

-- ---------------------------------------------------------
-- sources / sync logs / errors: admin & editor read; only
-- admin/super_admin write. Never exposed to anonymous/public.
-- ---------------------------------------------------------
create policy "sources_admin_editor_select" on sources
  for select using (has_role(array['admin','super_admin','editor']::app_role[]));
create policy "sources_admin_write" on sources
  for all using (has_role(array['admin','super_admin']::app_role[]))
  with check (has_role(array['admin','super_admin']::app_role[]));

create policy "sync_logs_admin_editor_select" on source_sync_logs
  for select using (has_role(array['admin','super_admin','editor']::app_role[]));
create policy "sync_logs_service_write" on source_sync_logs
  for all using (has_role(array['admin','super_admin']::app_role[]))
  with check (has_role(array['admin','super_admin']::app_role[]));
-- Note: the ingestion worker writes via the service-role key, which
-- bypasses RLS entirely — it never uses a user JWT.

create policy "source_errors_admin_editor_select" on source_errors
  for select using (has_role(array['admin','super_admin','editor']::app_role[]));

-- ---------------------------------------------------------
-- categories / tags / authors: public read, admin/editor write
-- ---------------------------------------------------------
create policy "categories_public_select" on categories for select using (true);
create policy "categories_editor_write" on categories
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

create policy "tags_public_select" on tags for select using (true);
create policy "tags_editor_write" on tags
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

create policy "authors_public_select" on authors for select using (true);
create policy "authors_editor_write" on authors
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

-- ---------------------------------------------------------
-- articles: public can read PUBLISHED only; staff can read/write
-- everything according to role. Nobody edits via anon/public role.
-- ---------------------------------------------------------
create policy "articles_public_select_published" on articles
  for select using (
    status = 'published'
    or has_role(array['admin','super_admin','editor','journalist','moderator']::app_role[])
  );

create policy "articles_journalist_insert" on articles
  for insert with check (has_role(array['admin','super_admin','editor','journalist']::app_role[]));

create policy "articles_editor_update" on articles
  for update using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

create policy "articles_admin_delete" on articles
  for delete using (has_role(array['admin','super_admin']::app_role[]));

-- join tables follow the article's own visibility/write rules
create policy "article_categories_public_select" on article_categories for select using (true);
create policy "article_categories_editor_write" on article_categories
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

create policy "article_tags_public_select" on article_tags for select using (true);
create policy "article_tags_editor_write" on article_tags
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

create policy "article_authors_public_select" on article_authors for select using (true);
create policy "article_authors_editor_write" on article_authors
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

create policy "article_sources_staff_select" on article_sources
  for select using (has_role(array['admin','super_admin','editor','journalist']::app_role[]));

create policy "media_public_select" on media for select using (true);
create policy "media_editor_write" on media
  for all using (has_role(array['admin','super_admin','editor','journalist']::app_role[]))
  with check (has_role(array['admin','super_admin','editor','journalist']::app_role[]));

create policy "article_media_public_select" on article_media for select using (true);
create policy "article_media_editor_write" on article_media
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

-- ---------------------------------------------------------
-- comments: any signed-in user can create; edit/delete only own;
-- moderators can moderate all; visible comments are public-readable.
-- ---------------------------------------------------------
create policy "comments_public_select_visible" on comments
  for select using (
    status = 'visible'
    or user_id = auth.uid()
    or has_role(array['admin','super_admin','moderator']::app_role[])
  );
create policy "comments_insert_own" on comments
  for insert with check (user_id = auth.uid());
create policy "comments_update_own_or_moderator" on comments
  for update using (user_id = auth.uid() or has_role(array['admin','super_admin','moderator']::app_role[]))
  with check (user_id = auth.uid() or has_role(array['admin','super_admin','moderator']::app_role[]));
create policy "comments_delete_own_or_moderator" on comments
  for delete using (user_id = auth.uid() or has_role(array['admin','super_admin','moderator']::app_role[]));

create policy "comment_reports_insert_own" on comment_reports
  for insert with check (reported_by = auth.uid());
create policy "comment_reports_select_moderator" on comment_reports
  for select using (has_role(array['admin','super_admin','moderator']::app_role[]));

-- ---------------------------------------------------------
-- bookmarks / reading_history / likes: strictly own-row only
-- ---------------------------------------------------------
create policy "bookmarks_owner_all" on bookmarks
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy "reading_history_owner_all" on reading_history
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy "likes_owner_all" on likes
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- ---------------------------------------------------------
-- notifications / subscriptions: own-row only (system/service
-- role inserts notifications; it bypasses RLS)
-- ---------------------------------------------------------
create policy "notifications_owner_select" on notifications
  for select using (user_id = auth.uid());
create policy "notifications_owner_update" on notifications
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy "subscriptions_owner_all" on subscriptions
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy "newsletter_public_insert" on newsletter_subscribers
  for insert with check (true); -- anyone can subscribe with their email
create policy "newsletter_owner_select" on newsletter_subscribers
  for select using (user_id = auth.uid() or has_role(array['admin','super_admin']::app_role[]));

-- ---------------------------------------------------------
-- advertisements: public can read ENABLED ads only; advertiser
-- can manage their own campaign; admin manages all.
-- ---------------------------------------------------------
create policy "ad_campaigns_owner_or_admin_select" on advertisement_campaigns
  for select using (advertiser_id = auth.uid() or has_role(array['admin','super_admin']::app_role[]));
create policy "ad_campaigns_owner_write" on advertisement_campaigns
  for insert with check (advertiser_id = auth.uid() or has_role(array['admin','super_admin']::app_role[]));
create policy "ad_campaigns_owner_update" on advertisement_campaigns
  for update using (advertiser_id = auth.uid() or has_role(array['admin','super_admin']::app_role[]))
  with check (advertiser_id = auth.uid() or has_role(array['admin','super_admin']::app_role[]));
create policy "ad_campaigns_admin_delete" on advertisement_campaigns
  for delete using (has_role(array['admin','super_admin']::app_role[]));

create policy "advertisements_public_select_enabled" on advertisements
  for select using (
    enabled = true
    or has_role(array['admin','super_admin']::app_role[])
    or exists (
      select 1 from advertisement_campaigns c
      where c.id = campaign_id and c.advertiser_id = auth.uid()
    )
  );
create policy "advertisements_owner_write" on advertisements
  for all using (
    has_role(array['admin','super_admin']::app_role[])
    or exists (select 1 from advertisement_campaigns c where c.id = campaign_id and c.advertiser_id = auth.uid())
  )
  with check (
    has_role(array['admin','super_admin']::app_role[])
    or exists (select 1 from advertisement_campaigns c where c.id = campaign_id and c.advertiser_id = auth.uid())
  );

-- ---------------------------------------------------------
-- settings / pages / menus / seo: public read, admin write
-- ---------------------------------------------------------
create policy "settings_public_select" on settings for select using (true);
create policy "settings_admin_write" on settings
  for all using (has_role(array['admin','super_admin']::app_role[]))
  with check (has_role(array['admin','super_admin']::app_role[]));

create policy "pages_public_select" on pages for select using (true);
create policy "pages_admin_write" on pages
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

create policy "menus_public_select" on menus for select using (true);
create policy "menus_admin_write" on menus
  for all using (has_role(array['admin','super_admin']::app_role[]))
  with check (has_role(array['admin','super_admin']::app_role[]));

create policy "seo_public_select" on seo_metadata for select using (true);
create policy "seo_editor_write" on seo_metadata
  for all using (has_role(array['admin','super_admin','editor']::app_role[]))
  with check (has_role(array['admin','super_admin','editor']::app_role[]));

-- ---------------------------------------------------------
-- analytics_events / article_views: insert-only for everyone
-- (privacy-conscious — no PII required), read restricted to staff
-- ---------------------------------------------------------
create policy "analytics_events_insert_any" on analytics_events
  for insert with check (true);
create policy "analytics_events_staff_select" on analytics_events
  for select using (has_role(array['admin','super_admin','editor']::app_role[]));

create policy "article_views_insert_any" on article_views
  for insert with check (true);
create policy "article_views_staff_select" on article_views
  for select using (has_role(array['admin','super_admin','editor']::app_role[]));

-- ---------------------------------------------------------
-- audit_logs: written by server/service role only; readable by
-- super_admin only
-- ---------------------------------------------------------
create policy "audit_logs_super_admin_select" on audit_logs
  for select using (has_role(array['super_admin']::app_role[]));
-- No insert/update/delete policy for regular JWTs at all — only the
-- service-role key (which bypasses RLS) or a SECURITY DEFINER function
-- should write here.

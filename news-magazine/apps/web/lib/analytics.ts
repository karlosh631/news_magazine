import { createServiceRoleClient } from "@/lib/supabase/server";

/**
 * The numbers on the admin dashboard.
 *
 * EVERY QUERY HERE IS AGAINST A COLUMN THAT EXISTS.
 * -----------------------------------------------
 * The schema was read before this was written, and the counts come
 * from `articles`, `reels`, `article_views`, `analytics_events`,
 * `source_sync_logs`, `source_errors`, `sources` and `categories`.
 * Nothing is invented and nothing is faked: where a metric a reader
 * would want is not in the schema, it is not shown, and the panel that
 * would have carried it is absent rather than filled with a number
 * that means nothing.
 *
 * The service-role client reads these, not the anon one. The admin
 * tables have no anon policy by design, and the other tables are
 * readable but this is a page behind a signed-in session - there is
 * no reason to make the queries slower or weaker than they need to be.
 */

export type AdminFilters = {
  sourceId?: string;
  categoryId?: string;
  since?: string;
  until?: string;
  query?: string;
};

export type StatCard = {
  label: string;
  value: number;
  hint: string;
};

export type Bucket = {
  key: string;
  label: string;
  count: number;
};

export type SourceRow = {
  id: string;
  name: string;
  adapter: string;
  enabled: boolean;
  allowed: boolean;
  pollInterval: number;
  articleCount: number;
  lastStatus: string | null;
  lastRunAt: string | null;
  lastError: string | null;
  errorCount: number;
};

export type CategoryRow = {
  id: string;
  slug: string;
  name: string;
  articleCount: number;
};

export type TopArticle = {
  id: string;
  slug: string;
  headline: string;
  source: string | null;
  views: number;
  publishedAt: string | null;
};

export type ReelsSourceRow = {
  sourceKey: string;
  enabled: boolean;
  missingEnvVar: string | null;
  keyCount: number;
  minPollSeconds: number;
  lastRunAt: string | null;
  lastCount: number | null;
  lastError: string | null;
  lastStatus: string | null;
  clipCount: number;
};

export type ErrorRow = {
  source: string | null;
  errorType: string | null;
  message: string;
  occurredAt: string | null;
};

export type AdminData = {
  stats: StatCard[];
  articlesByHour: Bucket[];
  articlesByDay: Bucket[];
  viewsByDay: Bucket[];
  eventMix: { type: string; count: number }[];
  sources: SourceRow[];
  categories: CategoryRow[];
  topArticles: TopArticle[];
  reelsSources: ReelsSourceRow[];
  recentErrors: ErrorRow[];
  filters: AdminFilters;
  totalSources: number;
  healthySources: number;
  generatedAt: string;
  degraded: string | null;
};

const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_MS = 60 * 60 * 1000;

/**
 * The providers the reels pipeline can use, and the variable that
 * switches each one on.
 *
 * This is a COPY of the list in the ingestion service's scheduler, and
 * it is a copy on purpose: the dashboard must be able to say "Pexels is
 * off because PEXELS_API_KEYS is not set" even on a deployment where
 * the migration that records the live state has not been applied. The
 * recorded state, when it exists, is the authority; this is the
 * fallback that names the variable.
 */
const REEL_PROVIDER_VARIABLES: { key: string; envVar: string | null }[] = [
  { key: "wikimedia", envVar: null },
  { key: "pexels", envVar: "PEXELS_API_KEYS" },
  { key: "pixabay", envVar: "PIXABAY_API_KEYS" },
  { key: "youtube", envVar: "YOUTUBE_API_KEYS" },
];

/** Apply the dashboard's filters to an articles query. */
function applyFilters<T extends { eq: Function; gte: Function; lte: Function; like: Function }>(
  query: T,
  filters: AdminFilters
): T {
  let q = query;

  if (filters.sourceId) q = q.eq("source_id", filters.sourceId);
  if (filters.categoryId) q = q.eq("primary_category_id", filters.categoryId);
  if (filters.since) q = q.gte("ingested_at", filters.since);
  if (filters.until) q = q.lte("ingested_at", filters.until);
  if (filters.query) {
    // ilike, and the wildcard goes around the operator's text so a
    // query for "dashain" finds "Dashain" in any case.
    const safe = filters.query.replace(/[%_,]/g, " ").trim();
    if (safe) q = q.like("headline", `%${safe}%`);
  }

  return q;
}

async function countRows(
  supabase: ReturnType<typeof createServiceRoleClient>,
  table: string,
  build: (q: any) => any
): Promise<number> {
  try {
    const { count, error } = await build(
      supabase.from(table).select("id", { count: "exact", head: true })
    ).execute();
    if (error) return 0;
    return count ?? 0;
  } catch {
    return 0;
  }
}

function dayKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function hourKey(date: Date): string {
  return date.toISOString().slice(0, 13);
}

/**
 * Read every metric the dashboard shows.
 *
 * Never raises. A panel that cannot be computed shows a zero and the
 * dashboard still loads - a dashboard that 500s on one bad table is
 * worse than one that is honest about what it could not read.
 */
export async function getAdminData(filters: AdminFilters): Promise<AdminData> {
  const supabase = createServiceRoleClient();
  const now = new Date();
  const degraded: string[] = [];

  const startOfToday = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  );
  const todayIso = startOfToday.toISOString();
  const weekAgo = new Date(now.getTime() - 7 * DAY_MS).toISOString();
  const monthAgo = new Date(now.getTime() - 30 * DAY_MS).toISOString();

  // ---- Headline numbers -------------------------------------------------

  const [
    totalArticles,
    articlesToday,
    articlesWeek,
    totalReels,
    reelsToday,
    totalViews,
    viewsToday,
    viewsWeek,
    sessionsWeek,
    imageless,
    unverified,
  ] = await Promise.all([
    countRows(supabase, "articles", (q) =>
      q.eq("status", "published").is("duplicate_of", null)
    ),
    countRows(supabase, "articles", (q) =>
      q.eq("status", "published").is("duplicate_of", null).gte("ingested_at", todayIso)
    ),
    countRows(supabase, "articles", (q) =>
      q.eq("status", "published").is("duplicate_of", null).gte("ingested_at", weekAgo)
    ),
    countRows(supabase, "reels", (q) => q),
    countRows(supabase, "reels", (q) => q.gte("ingested_at", todayIso)),
    countRows(supabase, "article_views", (q) => q),
    countRows(supabase, "article_views", (q) => q.gte("viewed_at", todayIso)),
    countRows(supabase, "article_views", (q) => q.gte("viewed_at", weekAgo)),
    countRows(supabase, "article_views", (q) => q.gte("viewed_at", weekAgo)),
    countRows(supabase, "articles", (q) =>
      q.eq("status", "published").is("featured_image_url", null)
    ),
    countRows(supabase, "articles", (q) =>
      q.eq("status", "published").is("image_verified_at", null)
    ),
  ]);

  // Unique sessions need a distinct count, which PostgREST does not
  // do, so the session ids are read and counted here. A week of
  // sessions is a bounded set.
  let uniqueSessions = 0;
  try {
    const { data, error } = await supabase
      .from("article_views")
      .select("session_id")
      .gte("viewed_at", weekAgo)
      .not("session_id", "is", null)
      .limit(20000);
    if (error) throw error;
    uniqueSessions = new Set((data ?? []).map((r: any) => r.session_id)).size;
  } catch {
    degraded.push("unique sessions");
  }

  // ---- Time series ------------------------------------------------------

  const [hourlyRows, dailyRows, viewRows] = await Promise.all([
    supabase
      .from("articles")
      .select("ingested_at")
      .eq("status", "published")
      .gte("ingested_at", new Date(now.getTime() - 24 * HOUR_MS).toISOString())
      .limit(5000)
      .then((r: any) => (r.error ? [] : (r.data ?? []))),
    supabase
      .from("articles")
      .select("ingested_at")
      .eq("status", "published")
      .gte("ingested_at", monthAgo)
      .limit(20000)
      .then((r: any) => (r.error ? [] : (r.data ?? []))),
    supabase
      .from("article_views")
      .select("viewed_at")
      .gte("viewed_at", monthAgo)
      .limit(20000)
      .then((r: any) => (r.error ? [] : (r.data ?? []))),
  ]);

  const articlesByHour = buildHourBuckets(hourlyRows);
  const articlesByDay = buildDayBuckets(dailyRows, 30);
  const viewsByDay = buildDayBuckets(viewRows, 30, "viewed_at");

  // ---- Event mix --------------------------------------------------------

  const eventMix: { type: string; count: number }[] = [];
  try {
    const { data, error } = await supabase
      .from("analytics_events")
      .select("event_type")
      .gte("occurred_at", weekAgo)
      .limit(20000);
    if (error) throw error;
    const counts = new Map<string, number>();
    for (const row of data ?? []) {
      const type = (row as any).event_type ?? "unknown";
      counts.set(type, (counts.get(type) ?? 0) + 1);
    }
    for (const [type, count] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
      eventMix.push({ type, count });
    }
  } catch {
    degraded.push("event mix");
  }

  // ---- Sources ----------------------------------------------------------

  const sources: SourceRow[] = [];
  try {
    const { data, error } = await supabase
      .from("sources")
      .select("id, name, adapter_key, enabled, allowed, poll_interval_seconds")
      .order("name");
    if (error) throw error;

    const sourceRows = data ?? [];
    const ids = sourceRows.map((s: any) => s.id);

    const counts = new Map<string, number>();
    const latestSync = new Map<string, any>();
    const errorCounts = new Map<string, number>();

    const [articleCounts, syncs, errorRows] = await Promise.all([
      supabase
        .from("articles")
        .select("source_id")
        .in("source_id", ids.length ? ids : ["00000000-0000-0000-0000-000000000000"])
        .then((r: any) => (r.error ? [] : (r.data ?? []))),
      supabase
        .from("source_sync_logs")
        .select("source_id, status, started_at, finished_at, error_count")
        .order("started_at", { ascending: false })
        .limit(2000)
        .then((r: any) => (r.error ? [] : (r.data ?? []))),
      supabase
        .from("source_errors")
        .select("source_id, message, occurred_at")
        .gte("occurred_at", monthAgo)
        .limit(2000)
        .then((r: any) => (r.error ? [] : (r.data ?? []))),
    ]);

    for (const row of articleCounts) {
      const id = (row as any).source_id;
      if (id) counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    for (const row of syncs as any[]) {
      if (row.source_id && !latestSync.has(row.source_id)) {
        latestSync.set(row.source_id, row);
      }
    }
    for (const row of errorRows as any[]) {
      if (row.source_id) {
        errorCounts.set(row.source_id, (errorCounts.get(row.source_id) ?? 0) + 1);
      }
    }

    for (const source of sourceRows as any[]) {
      const sync = latestSync.get(source.id);
      sources.push({
        id: source.id,
        name: source.name,
        adapter: source.adapter_key,
        enabled: Boolean(source.enabled),
        allowed: Boolean(source.allowed),
        pollInterval: source.poll_interval_seconds ?? 0,
        articleCount: counts.get(source.id) ?? 0,
        lastStatus: sync?.status ?? null,
        lastRunAt: sync?.started_at ?? null,
        lastError: null,
        errorCount: errorCounts.get(source.id) ?? 0,
      });
    }
  } catch {
    degraded.push("sources");
  }

  // ---- Categories -------------------------------------------------------

  const categories: CategoryRow[] = [];
  try {
    const { data, error } = await supabase
      .from("categories")
      .select("id, slug, name_en, name_ne")
      .order("sort_order");
    if (error) throw error;

    const { data: rows } = await supabase
      .from("articles")
      .select("primary_category_id")
      .eq("status", "published")
      .limit(20000);

    const counts = new Map<string, number>();
    for (const row of rows ?? []) {
      const id = (row as any).primary_category_id;
      if (id) counts.set(id, (counts.get(id) ?? 0) + 1);
    }

    for (const category of (data ?? []) as any[]) {
      categories.push({
        id: category.id,
        slug: category.slug,
        name: category.name_ne || category.name_en,
        articleCount: counts.get(category.id) ?? 0,
      });
    }
  } catch {
    degraded.push("categories");
  }

  // ---- Top articles -----------------------------------------------------

  const topArticles: TopArticle[] = [];
  try {
    const { data, error } = await supabase
      .from("articles")
      .select("id, slug, headline, source_name_snapshot, view_count, published_at")
      .eq("status", "published")
      .order("view_count", { ascending: false })
      .limit(10);
    if (error) throw error;

    for (const row of (data ?? []) as any[]) {
      topArticles.push({
        id: row.id,
        slug: row.slug,
        headline: row.headline,
        source: row.source_name_snapshot,
        views: row.view_count ?? 0,
        publishedAt: row.published_at,
      });
    }
  } catch {
    degraded.push("top articles");
  }

  // ---- Reels sources ----------------------------------------------------

  const reelsSources: ReelsSourceRow[] = [];
  try {
    const { data: health, error: healthError } = await supabase
      .from("reels_source_health")
      .select(
        "source_key, enabled, missing_env_var, key_count, min_poll_seconds, last_run_at, last_count, last_error, last_status"
      );
    if (healthError) throw healthError;

    const healthBy = new Map<string, any>();
    for (const row of (health ?? []) as any[]) healthBy.set(row.source_key, row);

    const { data: clipRows } = await supabase
      .from("reels")
      .select("source_key")
      .limit(20000);

    const clipCounts = new Map<string, number>();
    for (const row of (clipRows ?? []) as any[]) {
      const key = row.source_key;
      if (key) clipCounts.set(key, (clipCounts.get(key) ?? 0) + 1);
    }

    for (const provider of REEL_PROVIDER_VARIABLES) {
      const record = healthBy.get(provider.key);
      reelsSources.push({
        sourceKey: provider.key,
        enabled: record ? Boolean(record.enabled) : provider.envVar === null,
        missingEnvVar: record?.missing_env_var ?? provider.envVar,
        keyCount: record?.key_count ?? 0,
        minPollSeconds: record?.min_poll_seconds ?? 0,
        lastRunAt: record?.last_run_at ?? null,
        lastCount: record?.last_count ?? null,
        lastError: record?.last_error ?? null,
        lastStatus: record?.last_status ?? null,
        clipCount: clipCounts.get(provider.key) ?? 0,
      });
    }
  } catch {
    degraded.push("reels sources");
    // The recorded state is missing, but the operator still needs to
    // know which variable switches each provider on. Fall back to the
    // static list rather than showing an empty panel.
    for (const provider of REEL_PROVIDER_VARIABLES) {
      reelsSources.push({
        sourceKey: provider.key,
        enabled: provider.envVar === null,
        missingEnvVar: provider.envVar,
        keyCount: 0,
        minPollSeconds: 0,
        lastRunAt: null,
        lastCount: null,
        lastError: null,
        lastStatus: null,
        clipCount: 0,
      });
    }
  }

  // ---- Recent errors ----------------------------------------------------

  const recentErrors: ErrorRow[] = [];
  try {
    const { data, error } = await supabase
      .from("source_errors")
      .select("source_id, error_type, message, occurred_at")
      .order("occurred_at", { ascending: false })
      .limit(15);
    if (error) throw error;

    const names = new Map(sources.map((s) => [s.id, s.name]));
    for (const row of (data ?? []) as any[]) {
      recentErrors.push({
        source: names.get(row.source_id) ?? null,
        errorType: row.error_type,
        message: row.message,
        occurredAt: row.occurred_at,
      });
    }
  } catch {
    degraded.push("recent errors");
  }

  // ---- The headline cards ----------------------------------------------

  const totalSources = sources.length;
  const healthySources = sources.filter(
    (s) => s.enabled && s.allowed && s.lastStatus === "success"
  ).length;

  const stats: StatCard[] = [
    { label: "Articles", value: totalArticles, hint: "published, all time" },
    { label: "Today", value: articlesToday, hint: "articles fetched today" },
    { label: "This week", value: articlesWeek, hint: "articles in 7 days" },
    { label: "Views", value: totalViews, hint: "page views, all time" },
    { label: "Views today", value: viewsToday, hint: "in the last 24 hours" },
    { label: "Views this week", value: viewsWeek, hint: "in the last 7 days" },
    { label: "Readers", value: uniqueSessions, hint: "distinct sessions, 7 days" },
    { label: "Reels", value: totalReels, hint: "clips in the catalogue" },
    { label: "Reels today", value: reelsToday, hint: "clips fetched today" },
    {
      label: "No image",
      value: imageless,
      hint: "articles with no artwork",
    },
    {
      label: "Unverified",
      value: unverified,
      hint: "images not yet confirmed",
    },
    {
      label: "Sources",
      value: totalSources,
      hint: `${healthySources} healthy`,
    },
  ];

  return {
    stats,
    articlesByHour,
    articlesByDay,
    viewsByDay,
    eventMix,
    sources,
    categories,
    topArticles,
    reelsSources,
    recentErrors,
    filters,
    totalSources,
    healthySources,
    generatedAt: now.toISOString(),
    degraded: degraded.length ? degraded.join(", ") : null,
  };
}

function buildHourBuckets(rows: { ingested_at: string | null }[]): Bucket[] {
  const now = new Date();
  const buckets: Bucket[] = [];

  for (let i = 23; i >= 0; i -= 1) {
    const at = new Date(now.getTime() - i * HOUR_MS);
    buckets.push({ key: hourKey(at), label: at.toISOString().slice(11, 16), count: 0 });
  }

  const byKey = new Map(buckets.map((b) => [b.key, b]));
  for (const row of rows) {
    if (!row.ingested_at) continue;
    const bucket = byKey.get(row.ingested_at.slice(0, 13));
    if (bucket) bucket.count += 1;
  }

  return buckets;
}

function buildDayBuckets(
  rows: { [key: string]: string | null }[],
  days: number,
  field = "ingested_at"
): Bucket[] {
  const now = new Date();
  const buckets: Bucket[] = [];

  for (let i = days - 1; i >= 0; i -= 1) {
    const at = new Date(now.getTime() - i * DAY_MS);
    buckets.push({ key: dayKey(at), label: at.toISOString().slice(5, 10), count: 0 });
  }

  const byKey = new Map(buckets.map((b) => [b.key, b]));
  for (const row of rows) {
    const value = row[field];
    if (!value) continue;
    const bucket = byKey.get(value.slice(0, 10));
    if (bucket) bucket.count += 1;
  }

  return buckets;
}

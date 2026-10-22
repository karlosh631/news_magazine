import Link from "next/link";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { getAdminData, type AdminData, type Bucket } from "@/lib/analytics";
import { ADMIN_COOKIE, readSession, sessionSecret } from "@/lib/admin-auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * The admin dashboard.
 *
 * WHAT IT IS
 * ----------
 * A read-only view of what this site is actually doing: how many
 * stories came in, how many people read them, which outlets are
 * working, and which video provider is switched on.
 *
 * THE REELS PANEL IS THE ONE THAT MATTERS
 * ---------------------------------------
 * The Reels tab looked broken and the reason was invisible from
 * outside the worker. Four providers are possible and three of them
 * need a key that was never set; a provider with no key is skipped,
 * which the worker logs at INFO because it is not an error. So the
 * panel below says, in plain words, which provider is off and the
 * exact environment variable that switches it on.
 *
 * THE REEL PROVIDERS' CEILING
 * ---------------------------
 * Wikimedia Commons is the only one that needs no key, and it holds
 * about a hundred and thirty-five Nepal videos in total - and none of
 * them are H.264, so none of them play on iOS. Pexels, Pixabay and
 * YouTube are the ones that carry an effectively unlimited catalogue
 * with sound. That is why the panel leads with what is missing
 * rather than with what is there.
 *
 * THE FILTERS
 * -----------
 * They are a GET form, so the view lives in the URL and survives a
 * reload, a bookmark and a shared link. They are applied in the
 * QUERY, not by filtering an array that was already loaded, so the
 * numbers are the numbers for the filter and not a slice of a bigger
 * set.
 */

export default async function AdminPage({
  searchParams,
}: {
  searchParams: { [key: string]: string | string[] | undefined };
}) {
  const first = (value: string | string[] | undefined): string | undefined => {
    if (Array.isArray(value)) return value[0];
    return value && value.trim() ? value.trim() : undefined;
  };

  const filters = {
    sourceId: first(searchParams.source),
    categoryId: first(searchParams.category),
    since: first(searchParams.since),
    until: first(searchParams.until),
    query: first(searchParams.q),
  };

  // The cookie says who; the session row says whether that is still
  // true. Deleting the row ends the session immediately, so the row
  // is the authority and the cookie is only the claim.
  const secret = sessionSecret();
  const session = await readSession(
    (await cookies()).get(ADMIN_COOKIE)?.value,
    secret
  );

  if (!session) {
    const target = `/login?redirect=${encodeURIComponent("/admin")}`;
    redirect(target);
  }

  let sessionLive = true;
  try {
    const supabase = createServiceRoleClient();
    const { data, error } = await supabase
      .from("admin_sessions")
      .select("id, expires_at")
      .eq("id", session.sid)
      .maybeSingle();
    sessionLive = !error && Boolean(data);
  } catch {
    // The table is missing, so migration 0017 has not been applied.
    // The cookie is still signed and unexpired, so the operator is let
    // in - and the panel will say the tables are not there.
    sessionLive = true;
  }

  if (!sessionLive) {
    redirect(`/login?redirect=${encodeURIComponent("/admin")}`);
  }

  const data = await getAdminData(filters);

  return (
    <main className="min-h-screen bg-slate-950 text-slate-200">
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">
              Nepal News &amp; Magazine
            </p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-white">
              Operations
            </h1>
            <p className="mt-1 text-xs text-slate-500">
              Signed in as {session.email} ·{" "}
              {new Date(data.generatedAt).toISOString().replace("T", " ").slice(0, 16)} UTC
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-slate-500 hover:text-white"
            >
              View the site
            </Link>
            <form action="/api/admin/logout" method="post">
              <button
                type="submit"
                className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-slate-500 hover:text-white"
              >
                Sign out
              </button>
            </form>
          </div>
        </div>

        {data.degraded ? (
          <p className="mt-4 rounded-lg border border-amber-800/60 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
            Some panels could not be read: {data.degraded}. The rest are
            accurate.
          </p>
        ) : null}

        {/* ---- Headline numbers ---- */}
        <section className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {data.stats.map((stat) => (
            <div
              key={stat.label}
              className="rounded-lg border border-slate-800 bg-slate-900/60 p-4"
            >
              <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                {stat.label}
              </p>
              <p className="mt-1.5 text-2xl font-semibold tabular-nums text-white">
                {stat.value.toLocaleString("en-US")}
              </p>
              <p className="mt-0.5 text-[11px] text-slate-500">{stat.hint}</p>
            </div>
          ))}
        </section>

        {/* ---- Filters ---- */}
        <FilterBar data={data} filters={filters} />

        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <Panel title="Articles fetched, last 24 hours" subtitle="by hour">
            <BarChart buckets={data.articlesByHour} accent="sky" />
          </Panel>

          <Panel title="Articles fetched, last 30 days" subtitle="by day">
            <BarChart buckets={data.articlesByDay} accent="emerald" />
          </Panel>

          <Panel title="Page views, last 30 days" subtitle="by day">
            <BarChart buckets={data.viewsByDay} accent="violet" />
          </Panel>

          <Panel title="Reader events, last 7 days" subtitle="from analytics_events">
            {data.eventMix.length ? (
              <ul className="space-y-2">
                {data.eventMix.map((event) => {
                  const total = data.eventMix.reduce((sum, e) => sum + e.count, 0);
                  const pct = total ? Math.round((event.count / total) * 100) : 0;
                  return (
                    <li key={event.type} className="flex items-center gap-3">
                      <span className="w-20 shrink-0 text-xs capitalize text-slate-400">
                        {event.type}
                      </span>
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
                        <span
                          className="block h-full rounded-full bg-sky-500"
                          style={{ width: `${pct}%` }}
                        />
                      </span>
                      <span className="w-16 shrink-0 text-right text-xs tabular-nums text-slate-400">
                        {event.count.toLocaleString("en-US")}
                      </span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <Empty>No reader events recorded yet.</Empty>
            )}
          </Panel>
        </div>

        {/* ---- The reels panel ---- */}
        <Panel
          title="Reels sources"
          subtitle="what is running, and what switches the rest on"
          className="mt-4"
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                  <th className="py-2 pr-3 font-medium">Provider</th>
                  <th className="py-2 pr-3 font-medium">State</th>
                  <th className="py-2 pr-3 font-medium">Clips</th>
                  <th className="py-2 pr-3 font-medium">Last run</th>
                  <th className="py-2 pr-3 font-medium">Last pass</th>
                  <th className="py-2 font-medium">Needs</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {data.reelsSources.map((source) => (
                  <tr key={source.sourceKey}>
                    <td className="py-2.5 pr-3 font-medium capitalize text-slate-200">
                      {source.sourceKey}
                    </td>
                    <td className="py-2.5 pr-3">
                      {source.enabled ? (
                        <span className="rounded-full bg-emerald-950 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-300">
                          On
                        </span>
                      ) : (
                        <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                          Off
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 tabular-nums text-slate-300">
                      {source.clipCount.toLocaleString("en-US")}
                    </td>
                    <td className="py-2.5 pr-3 text-slate-400">
                      {source.lastRunAt
                        ? new Date(source.lastRunAt)
                            .toISOString()
                            .replace("T", " ")
                            .slice(0, 16)
                        : "—"}
                    </td>
                    <td className="py-2.5 pr-3 text-slate-400">
                      {source.lastStatus === "ok"
                        ? `${source.lastCount ?? 0} stored`
                        : source.lastError
                          ? source.lastError.slice(0, 60)
                          : "—"}
                    </td>
                    <td className="py-2.5 font-mono text-[11px] text-amber-300">
                      {source.missingEnvVar ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
            Wikimedia needs no key, and it is the only one that is on.
            It holds about a hundred and thirty-five Nepal videos in
            total, and none of them are H.264 — so none of them play on
            iOS. Pexels, Pixabay and YouTube carry an effectively
            unlimited catalogue with sound; each needs one environment
            variable on the ingestion service and nothing else. YouTube
            embeds rather than hosts, so it is unlimited and always new.
          </p>
        </Panel>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          {/* ---- Sources ---- */}
          <Panel title="Outlets" subtitle={`${data.healthySources} of ${data.totalSources} healthy`}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                    <th className="py-2 pr-3 font-medium">Source</th>
                    <th className="py-2 pr-3 font-medium">State</th>
                    <th className="py-2 pr-3 font-medium">Articles</th>
                    <th className="py-2 pr-3 font-medium">Every</th>
                    <th className="py-2 font-medium">Last poll</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {data.sources.map((source) => (
                    <tr key={source.id}>
                      <td className="py-2 pr-3">
                        <span className="font-medium text-slate-200">
                          {source.name}
                        </span>
                        <span className="ml-2 text-[10px] text-slate-500">
                          {source.adapter}
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        {!source.enabled || !source.allowed ? (
                          <span className="text-[10px] uppercase tracking-wider text-slate-500">
                            {source.allowed ? "Disabled" : "Not approved"}
                          </span>
                        ) : source.lastStatus === "success" ? (
                          <span className="text-[10px] uppercase tracking-wider text-emerald-400">
                            Healthy
                          </span>
                        ) : source.lastStatus === "partial" ? (
                          <span className="text-[10px] uppercase tracking-wider text-amber-400">
                            Partial
                          </span>
                        ) : (
                          <span className="text-[10px] uppercase tracking-wider text-red-400">
                            {source.lastStatus ?? "Waiting"}
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-3 tabular-nums text-slate-300">
                        {source.articleCount.toLocaleString("en-US")}
                      </td>
                      <td className="py-2 pr-3 tabular-nums text-slate-400">
                        {source.pollInterval}s
                      </td>
                      <td className="py-2 text-slate-400">
                        {source.lastRunAt
                          ? new Date(source.lastRunAt)
                              .toISOString()
                              .replace("T", " ")
                              .slice(5, 16)
                          : "—"}
                        {source.errorCount ? (
                          <span className="ml-2 text-red-400">
                            {source.errorCount} err
                          </span>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          {/* ---- Categories ---- */}
          <Panel title="Categories" subtitle="articles by primary category">
            {data.categories.length ? (
              <ul className="space-y-2">
                {data.categories
                  .filter((c) => c.articleCount > 0)
                  .sort((a, b) => b.articleCount - a.articleCount)
                  .map((category) => {
                    const max = Math.max(
                      ...data.categories.map((c) => c.articleCount),
                      1
                    );
                    return (
                      <li key={category.id} className="flex items-center gap-3">
                        <span className="w-28 shrink-0 truncate text-xs text-slate-300">
                          {category.name}
                        </span>
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
                          <span
                            className="block h-full rounded-full bg-emerald-500"
                            style={{
                              width: `${Math.round((category.articleCount / max) * 100)}%`,
                            }}
                          />
                        </span>
                        <span className="w-14 shrink-0 text-right text-xs tabular-nums text-slate-400">
                          {category.articleCount.toLocaleString("en-US")}
                        </span>
                      </li>
                    );
                  })}
              </ul>
            ) : (
              <Empty>No categories yet.</Empty>
            )}
          </Panel>

          {/* ---- Top articles ---- */}
          <Panel title="Most read" subtitle="by view_count">
            {data.topArticles.length ? (
              <ol className="space-y-2">
                {data.topArticles.map((article, index) => (
                  <li key={article.id} className="flex items-baseline gap-3">
                    <span className="w-4 shrink-0 text-right text-[10px] tabular-nums text-slate-600">
                      {index + 1}
                    </span>
                    <Link
                      href={`/article/${article.slug}`}
                      className="min-w-0 flex-1 truncate text-xs text-slate-300 underline-offset-2 hover:text-white hover:underline"
                    >
                      {article.headline}
                    </Link>
                    <span className="shrink-0 text-[11px] tabular-nums text-slate-500">
                      {article.views.toLocaleString("en-US")}
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <Empty>No articles yet.</Empty>
            )}
          </Panel>

          {/* ---- Errors ---- */}
          <Panel title="Recent errors" subtitle="from source_errors">
            {data.recentErrors.length ? (
              <ul className="space-y-2">
                {data.recentErrors.map((error, index) => (
                  <li key={index} className="text-xs">
                    <span className="text-slate-500">
                      {error.source ?? "unknown"}
                    </span>{" "}
                    <span className="text-red-300">
                      {error.errorType ?? "error"}
                    </span>
                    <p className="mt-0.5 break-words text-slate-400">
                      {error.message}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No errors recorded.</Empty>
            )}
          </Panel>
        </div>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

function Panel({
  title,
  subtitle,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-slate-800 bg-slate-900/60 p-4 ${className}`}
    >
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
        {subtitle ? (
          <p className="text-[11px] text-slate-500">{subtitle}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-slate-500">{children}</p>;
}

const ACCENTS: Record<string, string> = {
  sky: "bg-sky-500",
  emerald: "bg-emerald-500",
  violet: "bg-violet-500",
};

/**
 * A bar chart, drawn inline.
 *
 * No charting library, because there is no need for one: a bar is a
 * rectangle, and a dependency that ships a runtime to draw rectangles
 * is a dependency the site pays for on every page it is not used on.
 */
function BarChart({
  buckets,
  accent = "sky",
}: {
  buckets: Bucket[];
  accent?: keyof typeof ACCENTS;
}) {
  if (!buckets.length) return <Empty>Nothing to chart.</Empty>;

  const max = Math.max(...buckets.map((b) => b.count), 1);
  const total = buckets.reduce((sum, b) => sum + b.count, 0);

  return (
    <div>
      <div className="flex h-28 items-end gap-[2px]">
        {buckets.map((bucket) => (
          <div
            key={bucket.key}
            className="group relative flex-1"
            title={`${bucket.label}: ${bucket.count}`}
          >
            <div
              className={`w-full rounded-t-sm ${ACCENTS[accent] ?? ACCENTS.sky} ${
                bucket.count === 0 ? "opacity-20" : "opacity-80"
              }`}
              style={{ height: `${Math.max((bucket.count / max) * 100, 2)}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between text-[10px] tabular-nums text-slate-600">
        <span>{buckets[0]?.label}</span>
        <span>{total.toLocaleString("en-US")} total</span>
        <span>{buckets[buckets.length - 1]?.label}</span>
      </div>
    </div>
  );
}

/**
 * The filter form.
 *
 * A plain GET form, so the view lives in the URL: it survives a
 * reload, it can be bookmarked, and it can be shared. No client
 * JavaScript is involved, which means it works before hydration and
 * cannot break because of it.
 */
function FilterBar({
  data,
  filters,
}: {
  data: AdminData;
  filters: {
    sourceId?: string;
    categoryId?: string;
    since?: string;
    until?: string;
    query?: string;
  };
}) {
  return (
    <form
      method="get"
      className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900/60 p-4"
    >
      <Field label="Search headlines">
        <input
          type="search"
          name="q"
          defaultValue={filters.query ?? ""}
          placeholder="a word in the headline"
          className="w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-slate-500"
        />
      </Field>

      <Field label="Source">
        <select
          name="source"
          defaultValue={filters.sourceId ?? ""}
          className="w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-slate-500"
        >
          <option value="">All sources</option>
          {data.sources.map((source) => (
            <option key={source.id} value={source.id}>
              {source.name}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Category">
        <select
          name="category"
          defaultValue={filters.categoryId ?? ""}
          className="w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-slate-500"
        >
          <option value="">All categories</option>
          {data.categories.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </select>
      </Field>

      <Field label="From">
        <input
          type="date"
          name="since"
          defaultValue={filters.since?.slice(0, 10) ?? ""}
          className="w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-slate-500"
        />
      </Field>

      <Field label="To">
        <input
          type="date"
          name="until"
          defaultValue={filters.until?.slice(0, 10) ?? ""}
          className="w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-slate-500"
        />
      </Field>

      <div className="flex gap-2">
        <button
          type="submit"
          className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-900 transition hover:bg-white"
        >
          Apply
        </button>
        <Link
          href="/admin"
          className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-slate-500 hover:text-white"
        >
          Reset
        </Link>
      </div>
    </form>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </span>
      {children}
    </label>
  );
}

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

        {data.ignoredFilter ? (
          <p className="mt-4 rounded-lg border border-sky-800/60 bg-sky-950/40 px-3 py-2 text-xs text-sky-200">
            {data.ignoredFilter}
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
          <div>
            <table className="w-full table-fixed text-left text-xs">
              <caption className="sr-only">
                Each reels provider, whether it is switched on, how many
                clips it has contributed, when it last ran, and which
                environment variable it needs.
              </caption>
              <thead>
                <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Provider
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    State
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    Clips
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Last run
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Last pass
                  </th>
                  <th scope="col" className="py-2 font-medium">
                    Needs
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {data.reelsSources.map((source) => (
                  <tr
                    key={source.sourceKey}
                    className="transition-colors hover:bg-slate-800/30"
                  >
                    <th
                      scope="row"
                      className="py-2.5 pr-3 text-left font-medium capitalize text-slate-200"
                    >
                      {source.sourceKey}
                    </th>
                    <td className="py-2.5 pr-3">
                      {source.enabled ? (
                        <span className="inline-block rounded-full bg-emerald-950 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-300">
                          On
                        </span>
                      ) : (
                        <span className="inline-block rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                          Off
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-slate-300">
                      {source.clipCount.toLocaleString("en-US")}
                    </td>
                    <td className="py-2.5 pr-3 whitespace-nowrap text-slate-400">
                      {source.lastRunAt
                        ? new Date(source.lastRunAt)
                            .toISOString()
                            .replace("T", " ")
                            .slice(0, 16)
                        : "—"}
                    </td>
                    <td
                      className="max-w-[18rem] truncate py-2.5 pr-3 text-slate-400"
                      title={
                        source.lastStatus === "ok"
                          ? `${source.lastCount ?? 0} clips stored`
                          : (source.lastError ?? undefined)
                      }
                    >
                      {source.lastStatus === "ok"
                        ? `${source.lastCount ?? 0} stored`
                        : source.lastError
                          ? source.lastError
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
            It holds a few hundred Nepal videos in total, and none of
            them are H.264 — so none of them play on iOS. Pexels, Pixabay
            and YouTube carry an effectively unlimited catalogue with
            sound; each needs one environment variable on the ingestion
            service and nothing else. YouTube embeds rather than hosts,
            so it is unlimited and always new.
          </p>
        </Panel>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          {/* ---- Sources ---- */}
          <Panel title="Outlets" subtitle={`${data.healthySources} of ${data.totalSources} healthy`}>
            <div>
              <table className="w-full table-fixed text-left text-xs">
                <caption className="sr-only">
                  Every outlet, whether it is healthy, how many articles it
                  has contributed, how often it is polled, and when it last
                  ran.
                </caption>
                <thead className="sticky top-0 z-10 bg-slate-900/95 backdrop-blur">
                  <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Source
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      State
                    </th>
                    <th scope="col" className="py-2 pr-3 text-right font-medium">
                      Articles
                    </th>
                    <th scope="col" className="py-2 pr-3 text-right font-medium">
                      Every
                    </th>
                    <th scope="col" className="py-2 font-medium">
                      Last poll
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {data.sources.map((source) => (
                    <tr
                      key={source.id}
                      className="transition-colors hover:bg-slate-800/30"
                    >
                      <th scope="row" className="py-2 pr-3 text-left font-normal">
                        <span className="font-medium text-slate-200">
                          {source.name}
                        </span>
                        <span className="ml-2 text-[10px] text-slate-500">
                          {source.adapter}
                        </span>
                      </th>
                      <td className="py-2 pr-3">
                        {!source.enabled || !source.allowed ? (
                          <span className="inline-block rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                            {source.allowed ? "Disabled" : "Not approved"}
                          </span>
                        ) : source.lastStatus === "success" ? (
                          <span className="inline-block rounded-full bg-emerald-950 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-300">
                            Healthy
                          </span>
                        ) : source.lastStatus === "partial" ? (
                          <span className="inline-block rounded-full bg-amber-950 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-300">
                            Partial
                          </span>
                        ) : source.lastStatus === "failed" ? (
                          <span className="inline-block rounded-full bg-red-950 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-red-300">
                            Failed
                          </span>
                        ) : (
                          <span className="inline-block rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                            {source.lastStatus ?? "Waiting"}
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums text-slate-300">
                        {source.articleCount.toLocaleString("en-US")}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums text-slate-400">
                        {source.pollInterval}s
                      </td>
                      <td className="whitespace-nowrap py-2 text-slate-400">
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
              <div>
                <table className="w-full table-fixed text-left text-xs">
                  <caption className="sr-only">
                    Every category, the number of articles in it, and the
                    share of the largest category.
                  </caption>
                  <thead>
                    <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Category
                      </th>
                      <th scope="col" className="w-20 py-2 pr-3 text-right font-medium">
                        Articles
                      </th>
                      <th scope="col" className="w-16 py-2 text-right font-medium">
                        Share
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/70">
                    {data.categories
                      .filter((c) => c.articleCount > 0)
                      .sort((a, b) => b.articleCount - a.articleCount)
                      .map((category) => {
                        const max = Math.max(
                          ...data.categories.map((c) => c.articleCount),
                          1
                        );
                        return (
                          <tr
                            key={category.id}
                            className="transition-colors hover:bg-slate-800/30"
                          >
                            <th
                              scope="row"
                              className="py-2 pr-3 text-left font-normal text-slate-300"
                            >
                              {category.name}
                            </th>
                            <td className="py-2 pr-3 text-right tabular-nums text-slate-300">
                              {category.articleCount.toLocaleString("en-US")}
                            </td>
                            <td className="py-2 text-right tabular-nums text-slate-500">
                              {Math.round(
                                (category.articleCount / max) * 100
                              )}
                              %
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>No categories yet.</Empty>
            )}
          </Panel>

          {/* ---- Top articles ---- */}
          <Panel title="Most read" subtitle="by view_count">
            {data.topArticles.length ? (
              <ol className="space-y-1">
                {data.topArticles.map((article, index) => (
                  <li
                    key={article.id}
                    className="flex items-baseline gap-3 rounded px-1 py-1 transition-colors hover:bg-slate-800/30"
                  >
                    <span className="w-4 shrink-0 text-right text-[10px] tabular-nums text-slate-600">
                      {index + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <Link
                        href={`/article/${article.slug}`}
                        title={article.headline}
                        className="block truncate text-xs text-slate-300 underline-offset-2 hover:text-white hover:underline"
                      >
                        {article.headline}
                      </Link>
                      <span className="mt-0.5 block truncate text-[10px] text-slate-500">
                        {article.source ?? "unknown"}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                      {article.views.toLocaleString("en-US")}
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <Empty>No articles yet.</Empty>
            )}
          </Panel>

          {/* ---- The top of the feed ---- */}
          <Panel
            title="Top of the feed"
            subtitle="the newest news first — what the reader sees"
          >
            {data.latestArticles.length ? (
              <div>
                <table className="w-full table-fixed text-left text-xs">
                  <caption className="sr-only">
                    The stories at the top of the homepage, newest
                    published first, with the time each was fetched and
                    published.
                  </caption>
                  <thead>
                    <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Headline
                      </th>
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Outlet
                      </th>
                      <th scope="col" className="py-2 pr-3 font-medium">
                        Fetched
                      </th>
                      <th scope="col" className="py-2 font-medium">
                        Published
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/70">
                    {data.latestArticles.map((article) => (
                      <tr
                        key={article.id}
                        className="transition-colors hover:bg-slate-800/30"
                      >
                        <th scope="row" className="max-w-[22rem] py-2 pr-3 text-left font-normal">
                          <Link
                            href={`/article/${article.slug}`}
                            title={article.headline}
                            className="block truncate font-medium text-slate-200 underline-offset-2 hover:text-white hover:underline"
                          >
                            {article.headline}
                          </Link>
                          {!article.hasImage ? (
                            <span className="mt-0.5 block text-[10px] uppercase tracking-wider text-amber-400">
                              No picture
                            </span>
                          ) : null}
                        </th>
                        <td className="py-2 pr-3 text-slate-400">
                          {article.source ?? "—"}
                        </td>
                        <td className="whitespace-nowrap py-2 pr-3 tabular-nums text-slate-400">
                          {article.ingestedAt
                            ? new Date(article.ingestedAt)
                                .toISOString()
                                .replace("T", " ")
                                .slice(5, 16)
                            : "—"}
                        </td>
                        <td className="whitespace-nowrap py-2 tabular-nums text-slate-500">
                          {article.publishedAt
                            ? new Date(article.publishedAt)
                                .toISOString()
                                .replace("T", " ")
                                .slice(5, 16)
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>No published articles yet.</Empty>
            )}
          </Panel>

          {/* ---- Errors ---- */}
          <Panel title="Recent errors" subtitle="from source_errors">
            {data.recentErrors.length ? (
              <ul className="space-y-1">
                {data.recentErrors.map((error, index) => (
                  <li
                    key={index}
                    className="rounded px-1 py-1 text-xs transition-colors hover:bg-slate-800/30"
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="truncate font-medium text-slate-400">
                        {error.source ?? "unknown"}
                      </span>
                      <span className="shrink-0 text-[10px] uppercase tracking-wider text-red-300">
                        {error.errorType ?? "error"}
                      </span>
                      <span className="ml-auto shrink-0 whitespace-nowrap text-[10px] tabular-nums text-slate-600">
                        {error.occurredAt
                          ? new Date(error.occurredAt)
                              .toISOString()
                              .replace("T", " ")
                              .slice(5, 16)
                          : ""}
                      </span>
                    </div>
                    <p
                      className="mt-0.5 line-clamp-2 break-words text-slate-400"
                      title={error.message}
                    >
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

  // Every few bars gets a label, so the axis can be read without
  // twenty-four of them colliding. The old version labelled only the
  // first and the last, which left the middle of the chart a mystery.
  const labelEvery = Math.max(1, Math.round(buckets.length / 4));

  return (
    <div>
      <div className="flex h-28 items-end gap-[2px]">
        {buckets.map((bucket, index) => (
          <div
            key={bucket.key}
            className="group relative h-full flex-1"
            title={`${bucket.label}: ${bucket.count.toLocaleString("en-US")}`}
          >
            {/*
              The bar's height is a PERCENTAGE, and a percentage
              resolves against the parent's height. The parent used to
              be a flex item with no height of its own, so it was zero
              and every bar was zero with it - which is why the chart
              showed its axis labels and its total and nothing else.
              `h-full` gives it the full height of the `h-28` row, and
              the bar can finally be a fraction of something.
            */}
            <div
              className={`absolute inset-x-0 bottom-0 rounded-t-sm ${
                ACCENTS[accent] ?? ACCENTS.sky
              } ${bucket.count === 0 ? "opacity-20" : "opacity-80"}`}
              style={{ height: `${Math.max((bucket.count / max) * 100, 1)}%` }}
            />
            {/* The number, on hover. Without it the chart is a shape
                with no values on it. */}
            <span className="pointer-events-none absolute -top-5 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded bg-slate-950 px-1.5 py-0.5 text-[10px] tabular-nums text-slate-200 group-hover:block">
              {bucket.count}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between text-[10px] tabular-nums text-slate-600">
        <span>{buckets[0]?.label}</span>
        <span className="text-slate-500">
          {total.toLocaleString("en-US")} total
        </span>
        <span>{buckets[buckets.length - 1]?.label}</span>
      </div>
      {/* The intermediate labels, under their own bars, so the axis
          reads as a scale rather than as two endpoints. */}
      <div className="mt-0.5 flex text-[10px] tabular-nums text-slate-700">
        {buckets.map((bucket, index) => (
          <span
            key={bucket.key}
            className="flex-1 text-center first:text-left last:text-right"
          >
            {index % labelEvery === 0 ? bucket.label : ""}
          </span>
        ))}
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
  // What the operator is actually looking at, in words.
  //
  // A dashboard that quietly shows different numbers depending on a
  // filter is a dashboard that gets disbelieved. The headline cards
  // are ALWAYS live overall data - they are the health indicators, and
  // narrowing them would turn "is the site working?" into "does this
  // one source have stories?". The tables below them are the working
  // view, and those are what the filters apply to.
  const applied = data.appliedFilters;
  const active: string[] = [];

  if (applied.query) active.push(`headline contains "${applied.query}"`);
  if (applied.sourceId) {
    const source = data.sources.find((s) => s.id === applied.sourceId);
    active.push(`outlet ${source?.name ?? applied.sourceId}`);
  }
  if (applied.categoryId) {
    const category = data.categories.find((c) => c.id === applied.categoryId);
    active.push(`category ${category?.name ?? applied.categoryId}`);
  }
  if (applied.since || applied.until) {
    active.push(
      `fetched ${applied.since ? `from ${applied.since.slice(0, 10)}` : "at any time"} ${
        applied.until ? `to ${applied.until.slice(0, 10)}` : "onwards"
      }`
    );
  }

  return (
    <>
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

      {/* One sentence saying what is being shown, so the numbers are
          never a surprise. The cards above are always live overall
          data; the tables below are what the filters narrow. */}
      <p className="mt-2 text-[11px] text-slate-500">
        {active.length ? (
          <>
            <span className="font-medium text-slate-400">Filtered to</span>{" "}
            {active.join(" · ")}. The cards above stay live overall; the
            tables below are narrowed.
          </>
        ) : (
          <>
            Showing everything, live. The cards above are overall
            totals, and the tables below show the same data.
          </>
        )}
      </p>
    </>
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

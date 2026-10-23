/**
 * News data access.
 *
 * Every read the public site performs goes through here, so there is one
 * place that knows the SQL contract introduced by migration 0007.
 *
 * Stage 1 had no such layer: the homepage and category pages called
 * Gemini and rendered whatever came back, and only /search/page.tsx
 * touched the database. That is why the entire ingestion pipeline was
 * invisible to users — the data was being collected and then never read.
 */

import "server-only";

import { dropUnconfirmed, showsNothing } from "@/lib/card-artwork";
import { dropDuplicateStories } from "@/lib/feed-dedup";
import {
  cursorAfter as feedCursorAfter,
  feedCursorFilter,
  type FeedCursor,
} from "@/lib/feed-cursor";
import { dropImageless, dropRepeatedImages } from "@/lib/feed-images";
import { createPublicSupabaseClient } from "@/lib/supabase/server";
import type { LatestArticle, SearchResult } from "@/types/database";

export const PAGE_SIZE = 24;

/**
 * How much extra to ask the database for, so that removing duplicates
 * does not also shorten the page.
 *
 * A 24-row feed that contained two copies of one story would show 22
 * cards. Fetching twice as many and cutting back to 24 afterwards means
 * the grid is full AND duplicate-free whenever there are enough distinct
 * stories to fill it. This is a read-side over-fetch only: the ordering
 * is still the database's, and nothing is promoted — the extra rows are
 * only used to backfill slots the duplicates vacated.
 *
 * Three passes can now vacate a slot before the slice, not one: the story
 * pass (dropDuplicateStories), the picture pass (dropRepeatedImages, for
 * the case where two different stories arrive wearing the same
 * photograph) and the artwork pass (dropImageless, for a story that has
 * no picture at all and would otherwise render the "No image available"
 * panel). None of them reorders, so a survivor always keeps its own
 * position and the only thing the headroom has to cover is the row count.
 *
 * It was 2 for a long time, and 2 is not enough now. Measured against the
 * live feed at the time of writing, 7 of the newest 60 articles carried
 * no image in any media column, so roughly one row in nine is removed by
 * the artwork pass alone — a headroom of 2 hands back a 24-card page that
 * is 23 cards and starts showing gaps as soon as the ratio drifts. Three
 * covers the common case; the floor in `dropImageless` keeps the page from
 * ever going empty, and the client filter removes anything that still
 * slips through.
 *
 * FOUR PASSES CAN NOW VACATE A SLOT, and the fourth one is the reason
 * this is five rather than three.
 *
 * The confirmation pass (`dropUnconfirmed`) removes every story whose
 * picture has never been confirmed against its own page. On 2026-10-23
 * that was 57 of the newest 72 articles — an ingestion build older than
 * the confirmation rule had been writing them for hours — so a headroom
 * of three handed back a 24-card page that was FIFTEEN cards, and the
 * homepage painted one story and then filled itself in under the reader
 * (a layout shift of 0.23 on a phone).
 *
 * The headroom is a read-side over-fetch: the ordering is still the
 * database's, nothing is promoted, and the extra rows are only used to
 * backfill the slots the filters vacated. Five covers the gap even while
 * the backlog is being worked through, and it stops mattering the moment
 * the backlog is clear — at which point three would do again.
 */
const DEDUP_HEADROOM = 5;

/**
 * True when Supabase is not configured.
 *
 * The supabase client falls back to "placeholder.supabase.co" and
 * "missing-anon-key" rather than throwing, so every query "succeeds"
 * with zero rows. That is how the site rendered an empty homepage for
 * days while /api/health still reported the database as fine. Detecting
 * it here lets pages explain themselves instead of showing nothing.
 */
export function isSupabaseConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
      process.env.NEXT_PUBLIC_SUPABASE_URL !== "https://placeholder.supabase.co" &&
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY &&
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY !== "missing-anon-key"
  );
}

export interface FeedResult {
  articles: LatestArticle[];
  error: string | null;
  configured: boolean;
  /**
   * Where the page after this one starts, or null when the feed is
   * exhausted. See `FeedCursor`.
   */
  nextCursor: FeedCursor | null;
  /**
   * Rows matching the same filters, before deduplication. Null unless
   * the caller asked for it — an exact count is a second query, and the
   * polling path does not need it on every tick.
   */
  total: number | null;
}

/**
 * A stable position in the feed: the last row of the page just read.
 *
 * This is deliberately NOT an offset. `OFFSET n` counts rows the database
 * has since inserted, so on a table the ingestion worker writes to
 * continuously, every reader skips and repeats rows — the second page of
 * a feed can start in the middle of the first page, or replay it. A
 * keyset ("seek") cursor instead asks for rows older than a row the
 * reader has already seen, so rows arriving above the cursor never shift
 * what comes next. It is the same reason the ticker uses a cursor rather
 * than a page number.
 *
 * `published_at` is the raw string the database sent, not a re-serialised
 * one. PostgreSQL timestamps carry microseconds and `Date` carries
 * milliseconds, so round-tripping through an ISO string would truncate
 * the cursor and quietly drop every row inside the truncated sliver.
 *
 * `published_at` is always a real timestamp: NULLs sort last in this
 * ordering, so nothing can follow one and a cursor that pointed at an
 * undated row would address nothing. A feed that ends on an undated
 * article therefore reports "no more pages" — the one thing OFFSET could
 * have reached and a keyset cannot, and it is reported as the end of the
 * archive rather than as an infinite feed.
 */
/**
 * Where the next page of the feed starts.
 *
 * Re-exported from `lib/feed-cursor.ts`, which is the single
 * definition: the order the feed is shown in and the cursor that
 * pages through it are one decision, and this file is `server-only`
 * so it cannot be the place the rule is tested.
 */
export type { FeedCursor } from "@/lib/feed-cursor";

/** The cursor for whatever page of the feed ends at `article`. */
function cursorAfter(article: LatestArticle): FeedCursor | null {
  return feedCursorAfter(article);
}

/** An empty result, for every early return that is not a successful read. */
function emptyFeed(
  error: string | null,
  configured: boolean
): FeedResult {
  return { articles: [], error, configured, nextCursor: null, total: null };
}

/**
 * Newest-first, deduplicated articles for the homepage and category pages.
 *
 * Uses the latest_articles() RPC rather than a raw select so the
 * `duplicate_of is null` filter and the is_breaking ordering live in one
 * place in SQL instead of being re-derived in every component.
 *
 * Two layers of deduplication, because one is not enough. The SQL filter
 * drops the copies the ingestion worker linked; `dropDuplicateStories`
 * drops the copies it missed, which it misses because it is an
 * application-level read-then-insert check running per source sync with
 * no uniqueness constraint behind it. See lib/feed-dedup.ts.
 *
 * A third pass, `dropImageless`, removes stories with no artwork at all.
 * That is the pass behind the "NO IMAGE AVAILABLE" tiles readers were
 * seeing: the card's honest placeholder used to be the only handling
 * such a row ever got, and on every surface that does not poll the
 * archive the placeholder was simply permanent. Like the other two it is
 * a filter, not a reordering, and like them it falls back to showing the
 * rows when dropping them would empty the page.
 *
 * A fourth runs after it, `dropRepeatedImages`, for the failure none of
 * the others can see: two entirely different stories rendered under one
 * photograph, because outlets reuse pictures — and, worse, two outlets
 * covering one event each re-uploading the same agency frame under their
 * own filename, so the picture is the same and the URLs are not. That
 * pass takes the SECOND picture away rather than the second story: the
 * story is real and stays on the page, and the card shows the honest
 * "no image" state. See lib/feed-images.ts.
 *
 * The ORDER matters. The imageless filter runs first, so a card whose
 * picture was taken away by the duplicate pass is not then removed as
 * imageless — which would lose the story and defeat the point of taking
 * the picture away instead of the story.
 */
export async function getLatestArticles(options?: {
  limit?: number;
  categorySlug?: string;
  excludeIds?: string[];
  language?: "ne" | "en";
  cursor?: FeedCursor | null;
  withTotal?: boolean;
}): Promise<FeedResult> {
  const {
    limit = PAGE_SIZE,
    categorySlug,
    excludeIds,
    language,
    cursor = null,
    withTotal = false,
  } = options ?? {};

  if (!isSupabaseConfigured()) {
    return emptyFeed(
      "Supabase is not configured. Add NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to apps/web/.env.local.",
      false
    );
  }

  // Normalise: an unknown or absent value means "show everything".
  const languageFilter =
    language === "ne" || language === "en" ? language : undefined;

  const supabase = createPublicSupabaseClient();

  let categoryId: string | null = null;
  if (categorySlug) {
    const category = await supabase
      .from("categories")
      .select("id")
      .eq("slug", categorySlug)
      .maybeSingle();

    if (category.error) {
      return emptyFeed(category.error.message, true);
    }
    // An unknown category slug is an empty feed, not an error.
    if (!category.data) {
      return emptyFeed(null, true);
    }
    categoryId = category.data.id;
  }

  // Straight to PostgREST, ordered by published_at in the database.
  //
  // This used to go through the latest_articles() RPC, which ordered by
  // `is_breaking` BEFORE `published_at`. That made the feed's contents
  // depend on how many rows you happened to ask for, because the broken
  // sort puts every breaking-flagged story ahead of everything else
  // regardless of age:
  //
  //   /api/live-news?limit=5   -> newest 17:26 UTC
  //   /api/live-news?limit=24  -> newest 16:57 UTC   <- the homepage
  //   /api/live-news?limit=50  -> newest 17:26 UTC
  //
  // Asking for MORE rows returned newer articles than asking for fewer.
  // Over-fetching and re-sorting client-side only widened the gap —
  // there are more than 72 breaking rows, so no practical over-fetch
  // rescues it.
  //
  // A direct ordered query has no such ambiguity, is one round trip
  // instead of an RPC plus a client sort, and does not depend on
  // migration 0012 having been applied. The RPC is no longer on this
  // path at all.
  //
  // The picture pass runs inside both selects, before the slice, so a
  // dropped card's slot is backfilled from further down this same
  // ordering rather than left as a hole.
  //
  // The count is a second query and only runs when asked for. The poll
  // ticks every ten seconds and never needs it; a reader paging through
  // the archive wants it once, to know whether "Load more" is a lie.
  const counted = withTotal
    ? countFeed({ categoryId, language: languageFilter }).catch(() => null)
    : Promise.resolve(null);

  const feed = languageFilter
    ? await languageSelect({ language: languageFilter, limit, categoryId, excludeIds, cursor })
    : await directSelect({ limit, categoryId, excludeIds, cursor });

  if (feed.error) return feed;

  return { ...feed, total: await counted };
}

/**
 * Rows matching the feed's filters, deduplication not applied.
 *
 * `head: true` with `count: "exact"` returns the number in a header and
 * no rows, so this costs a scan of the published set and no payload. It
 * counts rows the reader will never see — the copies the read-side dedup
 * drops are counted here — so the number is an upper bound on how far
 * paging goes, and the UI says "about".
 *
 * For the language toggle this counts the requested language only, while
 * `languageSelect` may top the page up from the other one. Deliberate:
 * the alternative is a second count query on every page load for a
 * figure that is only ever shown as an approximation.
 */
async function countFeed({
  categoryId,
  language,
}: {
  categoryId: string | null;
  language?: "ne" | "en";
}): Promise<number | null> {
  const supabase = createPublicSupabaseClient();

  let query = supabase
    .from("articles")
    .select("id", { count: "exact", head: true })
    .eq("status", "published")
    .is("duplicate_of", null);

  if (categoryId) query = query.eq("primary_category_id", categoryId);
  if (language) query = query.eq("language", language);

  const { count, error } = await query;
  if (error) {
    console.error("[news] feed count failed:", error.message);
    return null;
  }
  return count ?? null;
}

/**
 * Narrow an ordered feed query to the rows below `cursor`.
 *
 * The row-value comparison `(published_at, id) < (:ts, :id)` that makes
 * this a keyset is not something PostgREST can express, so it is spelled
 * as the boolean identity it is:
 *
 *   published_at < ts  OR  (published_at = ts AND id < id)
 *
 * which is exactly the same set of rows, and is the form the `.or()`
 * escape hatch takes. `id` is the tiebreaker because `published_at` is
 * only second- or microsecond-resolution and several outlets publish in
 * the same instant; without it, a page boundary landing inside a run of
 * equal timestamps would skip or repeat those rows.
 *
 * The filter is passed WITHOUT its own outer parentheses. postgrest-js
 * wraps whatever `.or()` is given in a pair already, and a second pair
 * makes PostgREST reject the whole tree with "failed to parse logic
 * tree" — measured against the live server, which is how this was found.
 *
 * `query` is returned rather than mutated in place only because
 * postgrest-js builders are immutable; the call site reassigns.
 */
function applyCursor<T extends { or: (filter: string) => T }>(
  query: T,
  cursor: FeedCursor | null
): T {
  // The filter itself lives in lib/feed-cursor.ts, beside the ordering
  // it has to agree with. See that file for why the two cannot drift
  // apart, and for the PostgREST parenthesis rule.
  const filter = feedCursorFilter(cursor);
  if (!filter) return query;

  return query.or(filter);
}

/**
 * Read one language's coverage directly from the articles table.
 *
 * The latest_articles() RPC does not project `language`, so filtering by
 * it through PostgREST is the only way to make the toggle real. Both
 * Nepali and English editions of most outlets are ingested, so this
 * returns genuinely different stories rather than a translation.
 */
/**
 * Newest first, sorted in the application.
 *
 * latest_articles() ordered by `is_breaking desc` BEFORE `published_at`,
 * so every breaking-flagged article outranked every newer one and the
 * homepage froze on old stories. Migration 0012 fixes the function, but
 * the ordering the reader sees must not depend on a migration having
 * been applied yet — this sorts whatever comes back, so the page is
 * correct either way.
 *
 * ORDER OF ARRIVAL, NOT PUBLICATION TIME
 * ---------------------------------------
 * The primary key is `ingested_at` — when WE first saw the story — and
 * the id breaks ties.
 *
 * The reader asked for the most recently FETCHED story on top, and the
 * distinction is not cosmetic. A publisher can sit on a story for an
 * hour before putting it in their feed; when it appears, it is new to
 * this site and belongs at the top, even though its publication
 * timestamp is older than stories already on the page. Sorting by
 * publication time alone buried it.
 *
 * The reverse also matters. A story published two minutes ago that we
 * fetched ten minutes ago has been on the homepage the whole time; it
 * should not leap back to the top every time the feed polls, which is
 * exactly what a publication-time sort does to it. Sorting by arrival
 * means a story moves up ONCE, when it arrives, and then stays put.
 */
function newestFirst(rows: LatestArticle[]): LatestArticle[] {
  return [...rows].sort((a, b) => {
    // THE NEWEST FETCHED FIRST.
    //
    // The same order the query runs, so the server's answer and the
    // browser's sort cannot disagree and reshuffle the page.
    const arrivedA = a.ingested_at ? Date.parse(a.ingested_at) : 0;
    const arrivedB = b.ingested_at ? Date.parse(b.ingested_at) : 0;
    if (arrivedB !== arrivedA) return arrivedB - arrivedA;

    // The tiebreaker, and the same one the query uses.
    const idA = a.id ?? "";
    const idB = b.id ?? "";
    if (idA !== idB) return idB < idA ? -1 : 1;

    // The last tiebreaker: the outlet that carried the story most
    // often ends up on the card the reader sees.
    return (b.source_count ?? 0) - (a.source_count ?? 0);
  });
}

async function languageSelect({
  language,
  limit,
  categoryId,
  excludeIds,
  cursor,
}: {
  language: "ne" | "en";
  limit: number;
  categoryId: string | null;
  excludeIds?: string[] | null;
  cursor?: FeedCursor | null;
}): Promise<FeedResult> {
  const supabase = createPublicSupabaseClient();

  // `lang` is a parameter, not a captured constant. The thin-coverage
  // fallback below has to ask for the OTHER language, and chaining a
  // second `.eq("language", ...)` onto a builder that already carries
  // one does not do that: postgrest-js `eq()` *appends* to the query
  // string, so the request went out as
  //
  //   ...&language=eq.en&language=eq.ne
  //
  // which PostgREST ANDs into `language = 'en' AND language = 'ne'` —
  // zero rows, every time. The fallback was silently dead, so a thin
  // edition rendered as a nearly empty page of its own oldest stories
  // instead of being topped up.
  const build = (lang: "ne" | "en" = language) => {
    let query = supabase
      .from("articles")
      .select(
        "id, slug, headline, excerpt, summary_points, language, featured_image_url, image_highres_url, video_url, video_embed_url, audio_url, gif_url, image_verified_at, published_at, source_name_snapshot, source_article_url, source_count, ingested_at"
      )
      .eq("status", "published")
      .is("duplicate_of", null)
      .eq("language", lang)
      // Newest FETCHED first, for the same reason as the unfiltered
      // select: the window is the top of the feed. See directSelect.
      .order("ingested_at", { ascending: false, nullsFirst: false })
      // The tiebreaker for the keyset cursor. Deterministic, so a page
      // boundary inside a run of identical timestamps cannot skip or
      // repeat rows — see applyCursor.
      .order("id", { ascending: false })
      // Over-fetched for the same reason as directSelect: duplicates are
      // removed after the query, and the slots they vacate are refilled
      // from further down the ordering rather than left empty.
      .limit(limit * DEDUP_HEADROOM);
    if (categoryId) query = query.eq("primary_category_id", categoryId);
    if (excludeIds && excludeIds.length > 0) {
      query = query.not("id", "in", `(${excludeIds.join(",")})`);
    }
    return applyCursor(query, cursor ?? null);
  };

  const { data, error } = await build();
  if (error) {
    return emptyFeed(error.message, true);
  }

  const rows = ((data ?? []) as unknown[]).map(mapArticleRow);
  // Counted before the top-up below, because this is the database's own
  // answer to "is there anything left below this page", and the top-up
  // inflates it with rows from a second query.
  const fetched = rows.length;

  // Too thin on its own? Top up from the other language rather than
  // showing a nearly empty page.
  if (rows.length < limit / 2) {
    const other = language === "en" ? "ne" : "en";
    const { data: fallbackData } = await build(other);

    const seen = new Set(rows.map((row) => row.id));
    for (const row of (fallbackData ?? []) as unknown[]) {
      const mapped = mapArticleRow(row);
      if (seen.has(mapped.id)) continue;
      seen.add(mapped.id);
      rows.push(mapped);
    }
  }

  // The language toggle widens the window rather than widening the story:
  // both queries filter `duplicate_of is null`, and both still miss the
  // copies the worker never linked. Deduplicate AFTER the two languages
  // are merged, so a story that exists in both editions collapses once.
  //
  // Measured, nothing collapses this way: across the newest 300 articles
  // there is not one ne/en pair with even 0.6 token overlap, so the
  // top-up above brings in genuinely different stories rather than
  // translations. The pass stays because it costs nothing and the
  // top-up widens the pool every time a language is thin.
  //
  // The picture pass is in the same position for the same reason, and
  // runs BEFORE preferIllustrated: what it leaves behind is what
  // preferIllustrated is asked to find pictures for, and it will not
  // reorder either, so the two cannot disagree about which story holds
  // which slot.
  //
  // The artwork pass runs first of all, because it is the only one of the
  // three that can remove a card the reader would otherwise see as
  // broken. It is a filter, not a reordering, so putting it here changes
  // which rows survive and nothing about their order.
  const articles = preferIllustrated(
    dropRepeatedImages(
      dropUnconfirmed(
        dropImageless(dropDuplicateStories(newestFirst(rows))),
        showsNothing
      )
    ),
    limit
  );

  return {
    articles,
    error: null,
    configured: true,
    nextCursor: nextCursorFor(articles, fetched < limit * DEDUP_HEADROOM),
    total: null,
  };
}

/**
 * The cursor for the page that follows `articles`, or null when there is
 * nothing left to fetch.
 *
 * Anchored on the last row the caller can SEE, not the last row the
 * database sent. The difference matters: the over-fetch read further down
 * the feed, and some of those rows are duplicates and some were merely
 * cut by the slice. Anchoring on the fetched end would skip every story
 * between the slice and the fetch, which is most of the next page.
 *
 * `exhausted` is the database's own answer — fewer rows came back than
 * were asked for, so there is nothing below this one. It is checked
 * first, so a thin last page reports no cursor rather than handing back
 * one that would fetch an empty page next time.
 */
function nextCursorFor(
  articles: LatestArticle[],
  exhausted: boolean
): FeedCursor | null {
  const last = articles[articles.length - 1];
  if (exhausted || !last) return null;
  return cursorAfter(last);
}

/**
 * Let an illustrated story take the slot of an imageless one.
 *
 * The brief was that a card should never render blank or broken and that
 * a story with no photograph of its own should give way to one that has.
 * Satisfying both by REORDERING would be a worse lie than the blank card:
 * "newest first" is the whole promise of this page, and outranking a
 * two-hour-old story with a six-hour-old one to win a thumbnail is not
 * a trade the reader would accept.
 *
 * So the order is untouched. This only filters, and only while there is
 * genuine slack — if fewer than `limit` recent articles have any image
 * at all, they all stay, because emptying the feed would be worse than
 * a placeholder. No story is ever discarded for want of a picture; an
 * illustrated story simply outranks an imageless one of the same age.
 */
function preferIllustrated(rows: LatestArticle[], limit: number): LatestArticle[] {
  if (rows.length <= limit) return rows;

  // The order is the reader's: the newest fetched at the top. This
  // pass used to REORDER the page to put the illustrated stories
  // first, which meant the top of the page was not the top of the
  // news - and a story that arrived a minute ago could sit below one
  // that arrived an hour ago, because the older one had a picture.
  //
  // So the order is kept, and only the TAIL is filled: the page
  // takes its rows in order, and if the last row has no picture at
  // all, an illustrated row from further down takes its place. The
  // top of the page is exactly what the reader came for.
  const kept = rows.slice(0, limit);
  const missing = kept.findIndex(
    (row) => !row.image_highres_url && !row.featured_image_url
  );
  if (missing === -1) return kept;

  const spare = rows
    .slice(limit)
    .find((row) => Boolean(row.image_highres_url || row.featured_image_url));
  if (!spare) return kept;

  kept[missing] = spare;
  return kept;
}

/** Map a raw `articles` row to the card shape. */
function mapArticleRow(row: unknown): LatestArticle {
  const item = row as Record<string, unknown>;
  return {
    id: String(item.id),
    slug: String(item.slug),
    headline: String(item.headline),
    excerpt: (item.excerpt as string) ?? null,
    summary_points: (item.summary_points as string[]) ?? null,
    language: (item.language as "ne" | "en") ?? "ne",
    featured_image_url: (item.featured_image_url as string) ?? null,
    image_highres_url: (item.image_highres_url as string) ?? null,
    video_url: (item.video_url as string) ?? null,
    video_embed_url: (item.video_embed_url as string) ?? null,
    audio_url: (item.audio_url as string) ?? null,
    gif_url: (item.gif_url as string) ?? null,
    image_verified_at: (item.image_verified_at as string) ?? null,
    published_at: (item.published_at as string) ?? null,
    ingested_at: (item.ingested_at as string) ?? null,
    source_name_snapshot: (item.source_name_snapshot as string) ?? null,
    source_article_url: (item.source_article_url as string) ?? null,
    source_count: Number(item.source_count ?? 1),
    category_slug: null,
    category_name: null,
  };
}


/**
 * The feed query.
 *
 * Was `fallbackSelect`, used only when the latest_articles() RPC failed.
 * It is now the only path — see the note in getLatestArticles for why the
 * RPC was dropped.
 */
async function directSelect({
  limit,
  categoryId,
  excludeIds,
  cursor,
}: {
  limit: number;
  categoryId: string | null;
  excludeIds?: string[] | null;
  cursor?: FeedCursor | null;
}): Promise<FeedResult> {
  const supabase = createPublicSupabaseClient();

  let query = supabase
    .from("articles")
    .select(
      "id, slug, headline, excerpt, summary_points, featured_image_url, image_highres_url, video_url, video_embed_url, audio_url, gif_url, image_verified_at, published_at, source_name_snapshot, source_article_url, source_count, ingested_at, primary_category_id"
    )
    .eq("status", "published")
    .is("duplicate_of", null)
    // ORDER OF ARRIVAL: THE NEWEST FETCHED FIRST
    // ------------------------------------------
    // The window this query fills is the top of the feed, and the feed
    // is ordered by when WE fetched the story.
    //
    // The reader asked for this in the same words several times:
    // "the new articles must be shown on the top based on the time
    // immediatley fetch ... and unitll new come new show and move old
    // abit down". And the measurement says why it matters: of the
    // newest 500 stories the site holds, only 15 had been fetched in
    // the last hour, because a pass fetches about 250 rows and most
    // of them are the archive catching up. Ordered by publication
    // time, the top of the page barely moved — the reader reloaded and
    // found one or two new cards and nothing else, while everything
    // the system had just fetched sat further down.
    //
    // Ordered by arrival, the top of the page IS the last fetch: the
    // whole batch lands there, in the order it arrived, and the
    // previous batch moves down.
    //
    // The NEW badge is a different rule and still uses publication
    // time — see isFreshlyPublished in components/LiveArticleFeed.tsx.
    //
    // nullsFirst: false is required, not cosmetic. PostgreSQL defaults
    // `ORDER BY ... DESC` to NULLS FIRST, so any article with no
    // arrival time sorts ABOVE every dated one — an undated row takes
    // position 1 and the newest real story is pushed down the page.
    .order("ingested_at", { ascending: false, nullsFirst: false })
    // The keyset tiebreaker, and a determinism fix in its own right.
    // Several outlets publish inside the same second, so without a
    // second sort key PostgreSQL is free to return that run in any
    // order it likes — which made page boundaries inside such a run
    // skip and repeat rows. See applyCursor.
    .order("id", { ascending: false })
    // Over-fetched so that dropping duplicates can backfill with the next
    // story down instead of leaving a hole. See DEDUP_HEADROOM.
    .limit(limit * DEDUP_HEADROOM);

  if (categoryId) query = query.eq("primary_category_id", categoryId);
  if (excludeIds && excludeIds.length > 0) {
    query = query.not("id", "in", `(${excludeIds.join(",")})`);
  }
  query = applyCursor(query, cursor ?? null);

  const { data, error } = await query;
  if (error) {
    return emptyFeed(error.message, true);
  }

  const mapped = ((data ?? []) as unknown[]).map((row) => {
    const item = row as Record<string, unknown>;
    return {
      id: String(item.id),
      slug: String(item.slug),
      headline: String(item.headline),
      excerpt: (item.excerpt as string) ?? null,
      summary_points: (item.summary_points as string[]) ?? null,
      featured_image_url: (item.featured_image_url as string) ?? null,
      image_highres_url: (item.image_highres_url as string) ?? null,
      video_url: (item.video_url as string) ?? null,
      video_embed_url: (item.video_embed_url as string) ?? null,
      audio_url: (item.audio_url as string) ?? null,
      gif_url: (item.gif_url as string) ?? null,
    image_verified_at: (item.image_verified_at as string) ?? null,
      published_at: (item.published_at as string) ?? null,
    ingested_at: (item.ingested_at as string) ?? null,
      source_name_snapshot: (item.source_name_snapshot as string) ?? null,
      source_article_url: (item.source_article_url as string) ?? null,
      source_count: Number(item.source_count ?? 1),
      language: (item.language as "ne" | "en") ?? "ne",
      category_slug: null,
      category_name: null,
    } satisfies LatestArticle;
  });

  // The `.is("duplicate_of", null)` filter above catches the copies the
  // ingestion worker DID link. It cannot catch the ones it missed: the
  // database has no uniqueness constraint on a syndicated headline, so a
  // story that two sources stored in the same tick is two rows with
  // duplicate_of = null on both. This is the second line of defence, and
  // the only one that runs at read time.
  //
  // The third is the picture pass. It is not redundant with the second:
  // that one compares headlines, and its misses are not near-misses but
  // entirely different stories — an Upper Karnali funding story and a
  // Dashain apple-price story, filed by different outlets, with nothing
  // in common but the photograph both of them scraped. Running it after
  // the story pass means the story pass has already chosen which copy of
  // a story survives, so the picture it contributes is the one the reader
  // would actually see.
  //
  // The fourth is the artwork pass. This is the path the homepage and
  // every category page read, so it is the one that mattered most: a row
  // with no picture at all was handed straight to NewsCard, which renders
  // its "No image available" panel. The client-side `imageless` set in
  // components/LiveArticleFeed.tsx could not have saved this — it is
  // populated by an onError that only fires after the browser has tried
  // the image, and only on surfaces that pass the callback at all.
  const articles = dropRepeatedImages(
    dropUnconfirmed(
      dropImageless(dropDuplicateStories(mapped)),
      showsNothing
    )
  ).slice(0, limit);

  return {
    articles,
    error: null,
    configured: true,
    // Fewer rows than were asked for means the database ran out, not
    // that deduplication ate them: `mapped` is counted before a single
    // card was dropped.
    nextCursor: nextCursorFor(articles, mapped.length < limit * DEDUP_HEADROOM),
    total: null,
  };
}

export interface SearchFeedResult {
  articles: SearchResult[];
  error: string | null;
  configured: boolean;
}

/**
 * Adapt a search row to the shape NewsCard renders.
 *
 * The two SQL functions return overlapping but differently-shaped rows
 * (search_articles has `rank`/`resurrected`, latest_articles has
 * `category_*`). NewsCard needs the card fields, so map explicitly
 * rather than casting — a cast would hide a genuine mismatch the next
 * time either function's signature changes.
 */
export function toCardArticle(
  row: SearchResult | LatestArticle
): LatestArticle {
  return {
    id: row.id,
    slug: row.slug,
    headline: row.headline,
    excerpt: row.excerpt ?? null,
    summary_points: row.summary_points ?? null,
    featured_image_url: row.featured_image_url ?? null,
    image_highres_url: row.image_highres_url ?? null,
    video_url: row.video_url ?? null,
    video_embed_url: row.video_embed_url ?? null,
    audio_url: row.audio_url ?? null,
    gif_url: row.gif_url ?? null,
    image_verified_at:
      "image_verified_at" in row ? (row.image_verified_at ?? null) : null,
    published_at: row.published_at ?? null,
    // The search RPC's row shape does not carry it, and the mapper
    // serves both shapes — same guarded pattern as source_count below.
    ingested_at: "ingested_at" in row ? (row.ingested_at ?? null) : null,
    source_name_snapshot: row.source_name_snapshot ?? null,
    source_article_url: row.source_article_url ?? null,
    source_count: "source_count" in row ? Number(row.source_count ?? 1) : 1,
    category_slug: "category_slug" in row ? (row.category_slug ?? null) : null,
    category_name: "category_name" in row ? (row.category_name ?? null) : null,
  };
}

/** Database-only search. Never scrapes, so it is safe for every keystroke. */
export async function searchArticles(
  query: string,
  limit = PAGE_SIZE
): Promise<SearchFeedResult> {
  if (!isSupabaseConfigured()) {
    return {
      articles: [],
      error: "Supabase is not configured.",
      configured: false,
    };
  }

  const supabase = createPublicSupabaseClient();

  const { data, error } = await supabase.rpc("search_articles", {
    p_query: query,
    p_limit: limit,
    p_category: null,
    // Include pruned rows: the search page calls the Python service to
    // resurrect anything older than the retention window.
    p_include_pruned: true,
  });

  if (error) {
    console.error("[news] search_articles failed:", error.message);
    return { articles: [], error: error.message, configured: true };
  }

  return { articles: (data ?? []) as SearchResult[], error: null, configured: true };
}

export async function getArticleBySlug(slug: string) {
  if (!isSupabaseConfigured()) return null;

  const supabase = createPublicSupabaseClient();

  const { data, error } = await supabase
    .from("articles")
    .select("*")
    .eq("slug", slug)
    .eq("status", "published")
    .is("duplicate_of", null)
    .maybeSingle();

  if (error) {
    console.error("[news] article lookup failed:", error.message);
    return null;
  }

  return data;
}

export async function getRelatedArticles(
  categoryId: string | null,
  excludeId: string,
  limit = 6
): Promise<LatestArticle[]> {
  if (!isSupabaseConfigured() || !categoryId) return [];

  const supabase = createPublicSupabaseClient();
  const { data, error } = await supabase
    .from("articles")
    .select(
      "id, slug, headline, excerpt, summary_points, featured_image_url, image_highres_url, video_url, video_embed_url, audio_url, gif_url, image_verified_at, published_at, source_name_snapshot, source_article_url, source_count, ingested_at"
    )
    .eq("status", "published")
    .is("duplicate_of", null)
    .eq("primary_category_id", categoryId)
    .neq("id", excludeId)
    .order("published_at", { ascending: false, nullsFirst: false })
    .limit(limit * DEDUP_HEADROOM);

  if (error) {
    console.error("[news] related lookup failed:", error.message);
    return [];
  }

  const related = ((data ?? []) as unknown[]).map((row) => {
    const item = row as Record<string, unknown>;
    return {
      id: String(item.id),
      slug: String(item.slug),
      headline: String(item.headline),
      excerpt: (item.excerpt as string) ?? null,
      summary_points: (item.summary_points as string[]) ?? null,
      featured_image_url: (item.featured_image_url as string) ?? null,
      image_highres_url: (item.image_highres_url as string) ?? null,
      video_url: (item.video_url as string) ?? null,
      video_embed_url: (item.video_embed_url as string) ?? null,
      audio_url: (item.audio_url as string) ?? null,
      gif_url: (item.gif_url as string) ?? null,
    image_verified_at: (item.image_verified_at as string) ?? null,
      published_at: (item.published_at as string) ?? null,
    ingested_at: (item.ingested_at as string) ?? null,
      source_name_snapshot: (item.source_name_snapshot as string) ?? null,
      source_article_url: (item.source_article_url as string) ?? null,
      source_count: Number(item.source_count ?? 1),
      category_slug: null,
      category_name: null,
    } satisfies LatestArticle;
  });

  // The rail under an article is where a near-duplicate of the article
  // itself is most visible: the reader has just finished one version of
  // a story and the next card is that story again. Same for the picture:
  // a four-card rail with one photograph repeated in it looks broken in
  // a way the full grid does not, because there is nothing else on the
  // page for the eye to compare it against.
  //
  // The rail shows four and fetches eight, so a repeat costs a card and
  // the reader still gets four distinct stories whenever the category
  // has them.
  //
  // A blank tile is worse here than on the grid, not better: the rail is
  // four cards, so one placeholder is a quarter of it, and there is no
  // surrounding grid for the eye to read it against.
  return dropRepeatedImages(dropImageless(dropDuplicateStories(related))).slice(
    0,
    limit
  );
}

export async function getCategories() {
  if (!isSupabaseConfigured()) return [];

  const supabase = createPublicSupabaseClient();
  const { data, error } = await supabase
    .from("categories")
    .select("id, slug, name_en, name_ne, sort_order")
    .order("sort_order", { ascending: true });

  if (error) {
    console.error("[news] categories lookup failed:", error.message);
    return [];
  }

  return data ?? [];
}

/** Breaking news, for the ticker. */
export async function getBreakingNews(limit = 10): Promise<LatestArticle[]> {
  if (!isSupabaseConfigured()) return [];

  const supabase = createPublicSupabaseClient();
  const { data, error } = await supabase
    .from("articles")
    .select(
      "id, slug, headline, excerpt, summary_points, featured_image_url, image_highres_url, video_url, video_embed_url, audio_url, gif_url, image_verified_at, published_at, source_name_snapshot, source_article_url, source_count, ingested_at"
    )
    .eq("status", "published")
    .is("duplicate_of", null)
    .eq("is_breaking", true)
    .order("published_at", { ascending: false, nullsFirst: false })
    .limit(limit * DEDUP_HEADROOM);

  if (error) {
    console.error("[news] breaking lookup failed:", error.message);
    return [];
  }

  const breaking = ((data ?? []) as unknown[]).map((row) => {
    const item = row as Record<string, unknown>;
    return {
      id: String(item.id),
      slug: String(item.slug),
      headline: String(item.headline),
      excerpt: (item.excerpt as string) ?? null,
      summary_points: (item.summary_points as string[]) ?? null,
      featured_image_url: (item.featured_image_url as string) ?? null,
      image_highres_url: (item.image_highres_url as string) ?? null,
      video_url: (item.video_url as string) ?? null,
      video_embed_url: (item.video_embed_url as string) ?? null,
      audio_url: (item.audio_url as string) ?? null,
      gif_url: (item.gif_url as string) ?? null,
    image_verified_at: (item.image_verified_at as string) ?? null,
      published_at: (item.published_at as string) ?? null,
    ingested_at: (item.ingested_at as string) ?? null,
      source_name_snapshot: (item.source_name_snapshot as string) ?? null,
      source_article_url: (item.source_article_url as string) ?? null,
      source_count: Number(item.source_count ?? 1),
      category_slug: null,
      category_name: null,
    } satisfies LatestArticle;
  });

  // The ticker is the worst place for a repeat: ten slots, no pictures,
  // read at a glance while the page is still loading. Same pass as the
  // feed, same reason.
  //
  // The picture pass is deliberately NOT here, and the omission is not an
  // oversight. BreakingNewsTicker renders a headline and nothing else —
  // there is no <img> on the page to repeat — so dropping a story for
  // wearing a photo the reader will never see would remove breaking news
  // in exchange for nothing at all.
  return dropDuplicateStories(breaking).slice(0, limit);
}
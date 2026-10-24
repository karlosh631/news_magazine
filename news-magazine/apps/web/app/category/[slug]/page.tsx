import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import { LiveArticleFeed } from "@/components/LiveArticleFeed";
import { PanchangPanel } from "@/components/PanchangPanel";
import { ReelsFeed } from "@/components/ReelsFeed";
import {
  getPanchangDays,
  getPanchangFor,
  getRashifalFor,
  kathmanduToday,
} from "@/lib/daily-info";
import { getCategories, getLatestArticles } from "@/lib/news";
import { getReels } from "@/lib/reels";
import {
  fetchRashifalPeriod,
  RASHIFAL_PERIODS,
  readPeriod,
} from "@/lib/rashifal-periods";
import { SITE_NAME, SITE_URL, jsonLd } from "@/lib/site";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ slug: string }>;
  /** The day and the period being asked for, on the Rashifal page. */
  searchParams?: Promise<{ date?: string; period?: string }>;
}

export async function generateStaticParams() {
  const categories = await getCategories();
  return categories.map((category) => ({ slug: category.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const categories = await getCategories();
  const category = categories.find(
    (item) => item.slug.toLowerCase() === slug.toLowerCase()
  );

  if (!category) {
    return { title: "Category not found" };
  }

  const name = category.name_ne ?? category.name_en;
  const description = `Latest ${category.name_en.toLowerCase()} news from across Nepal, aggregated from more than 20 Nepali outlets and deduplicated.`;

  return {
    // The site name comes from layout.tsx's title.template — appending
    // it here duplicated it in every category title.
    title: `${category.name_en} news`,
    description,
    alternates: { canonical: `/category/${category.slug}` },
    openGraph: {
      title: `${name} — latest news`,
      description,
      type: "website",
      url: `${SITE_URL}/category/${category.slug}`,
      siteName: SITE_NAME,
    },
    twitter: {
      card: "summary_large_image",
      title: `${name} — latest news`,
      description,
    },
  };
}

/**
 * Category page.
 *
 * Stage 1 validated the slug against a hardcoded CATEGORY_NAMES map and
 * then returned `<LiveNewsFeed category={slug} />` — the Gemini-
 * fabricated feed — before ever reaching the ~300 lines of database
 * query code that followed the return. Those lines were unreachable, so
 * no category page ever showed a real article, and any slug outside the
 * six hardcoded names 404'd even though the categories table holds
 * eleven.
 *
 * Now it reads the categories table for the slug and queries the
 * deduplicated articles for that category.
 */
export default async function CategoryPage({ params, searchParams }: Props) {
  const { slug: rawSlug } = await params;
  const slug = rawSlug?.toLowerCase();

  if (!slug) notFound();

  const categories = await getCategories();
  const category = categories.find(
    (item) => item.slug.toLowerCase() === slug
  );

  if (!category) notFound();

  /*
   * Reels are a category in the nav, the footer and the sitemap like any
   * other — migration 0014 inserts the row — but they are not articles.
   * Articles are never filed under it, so the normal query below would
   * return an empty feed and this page would claim, on every visit, that
   * there were no stories in Reels. The one category whose body is not
   * a grid gets its own body.
   *
   * This is the ONLY branch that reads differently. Everything after it
   * is untouched, and no other slug can reach it.
   */
  if (slug === "rashifal") {
    // The day's almanac.
    //
    // A Nepali reader checks the panchang before they read the news,
    // and the site had nowhere to put it. The values are fetched once
    // a day by the worker, stored, and read here - so the page shows
    // what the almanac actually says, not something computed on the
    // spot.
    //
    // AND THE DAYS AHEAD
    // ------------------
    // "Today's tithi" is one question and "tomorrow's tithi" is
    // another, and only the first of them has a row in the table
    // unless the worker keeps the week ahead. So the worker does,
    // and the page offers the days it has: today by default, and
    // the days after it as a row of buttons.
    //
    // The day is in the URL (`?date=2026-10-23`), so a reader can
    // send tomorrow's horoscope to somebody and they will see the
    // same day rather than today's.
    const today = kathmanduToday();
    const requested = (await searchParams)?.date;

    // The days that really are stored. The list is the truth about
    // what can be shown, so a day that is not in it is not offered.
    const days = await getPanchangDays(today, 14);

    // A day is only shown if it is one the worker stored. Asking for
    // a day that is not there falls back to today rather than
    // showing an empty page: a reader who typed a wrong date gets
    // the almanac, not a blank.
    const known = days.some((day) => day.date === requested);
    const date = known ? (requested as string) : today;

    /*
      THE FOUR PERIODS
      ----------------
      The source's own Rashifal page carries four tabs - daily,
      weekly, monthly and yearly - and the reader asked for all four:
      the site could answer one of their four questions.

      The daily tab is the site's OWN copy, read from `daily_rashifal`
      (the ingestion stores twelve rows a day), so it never depends on
      the publisher answering. The other three are read from the
      source when the page is built and cached for an hour.
    */
    const period = readPeriod((await searchParams)?.period);

    const [panchang, dailySigns, periodSigns] = await Promise.all([
      getPanchangFor(date),
      getRashifalFor(date),
      fetchRashifalPeriod(period),
    ]);

    // The daily tab shows the stored horoscopes; the other three show
    // the period's own.
    const signs = period === "daily" ? dailySigns : periodSigns;

    /*
      WHAT THIS PAGE IS, FOR A MACHINE
      ---------------------------------
      The almanac is a collection: the five limbs of the panchang for
      one day, and then the twelve horoscopes for that day. A
      CollectionPage says that, and the ItemList inside it names the
      twelve signs the page actually carries - no more, no fewer.

      It matters here more than on the other category pages, because
      this route returns before the feed that emits the other
      category pages' JSON-LD ever runs. Without this block the
      page says nothing at all about what it holds.

      Everything in it is something the page shows: the title is the
      heading above, the date is the day being shown, and the twelve
      names are the twelve signs the worker stored. Nothing is
      invented, and the canonical URL is the one the category's own
      metadata declares.
    */
    const pageTitle =
      date === today ? "आजको पञ्चाङ र राशिफल" : `पञ्चाङ र राशिफल — ${date}`;

    return (
      <main className="min-h-screen">
        <section className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
          <script
            type="application/ld+json"
            dangerouslySetInnerHTML={{
              __html: jsonLd({
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                name: pageTitle,
                url: `${SITE_URL}/category/rashifal`,
                datePublished: date,
                isPartOf: {
                  "@type": "WebSite",
                  name: SITE_NAME,
                  url: SITE_URL,
                },
                mainEntity: {
                  "@type": "ItemList",
                  itemListElement: signs.map((sign, index) => ({
                    "@type": "ListItem",
                    position: index + 1,
                    name: sign.nameNe ?? sign.name ?? `Sign ${sign.sign}`,
                  })),
                },
              }),
            }}
          />
          {/*
            The page's name.

            The panel carries the day's own heading, and the panel's
            heading is an h2 — a page whose top heading is a level two
            is a page whose subject a screen reader and a crawler both
            have to guess. This is visually hidden rather than absent,
            so the reader sees the same page and the machine gets a
            heading.
          */}
          <h1 className="sr-only">{pageTitle}</h1>

          {/*
            THE FOUR TABS

            The source's own page has them, and so does this one: a
            reader asking for "this week's Scorpio" is asking a
            different question from one asking for "today's Scorpio",
            and the site could only answer one of them.

            The period is in the URL (`?period=weekly`), so the link can
            be sent to somebody and they will see the same week.
          */}
          <nav
            aria-label="राशिफलको अवधि"
            className="mb-6 flex flex-wrap gap-2"
          >
            {RASHIFAL_PERIODS.map((entry) => {
              const active = entry.key === period;
              return (
                <Link
                  key={entry.key}
                  href={`/category/rashifal?period=${entry.key}`}
                  aria-current={active ? "page" : undefined}
                  className={
                    active
                      ? "inline-flex min-h-[40px] items-center rounded-full border border-blue-600 bg-blue-600 px-4 text-sm font-semibold text-white"
                      : "inline-flex min-h-[40px] items-center rounded-full border border-gray-300 px-4 text-sm font-semibold text-gray-700 transition hover:border-blue-600 hover:text-blue-700 dark:border-gray-600 dark:text-gray-200 dark:hover:border-blue-400"
                  }
                >
                  {entry.label}
                </Link>
              );
            })}
          </nav>

          <PanchangPanel
            panchang={panchang}
            signs={signs}
            days={days}
            selectedDate={date}
            today={today}
            weekday={new Date(`${date}T00:00:00Z`).toLocaleDateString(
              "en-GB",
              { weekday: "long", timeZone: "UTC" }
            )}
            weekdayNe=""
          />
          {panchang?.sourceUrl ? (
            <p className="mt-4 text-xs text-gray-400 dark:text-gray-500">
              Source:{" "}
              <a
                href={panchang.sourceUrl}
                rel="noopener noreferrer nofollow"
                target="_blank"
                className="underline underline-offset-2 hover:text-gray-600 dark:hover:text-gray-300"
              >
                {panchang.sourceUrl}
              </a>
            </p>
          ) : null}
        </section>
      </main>
    );
  }

  if (slug === "reels") {
    // The whole catalogue, not a page of it.
    //
    // The panel reports how many clips are stored, and the page used
    // to show twelve of them with no way to reach the rest — so a
    // reader saw the same twelve for ever and had no reason to
    // believe the catalogue was any bigger. The feed now rotates
    // through everything it is given.
    const reels = await getReels({ limit: 200 });

    if (reels.reels.length === 0) {
      return (
        <main className="min-h-screen">
          <section className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
            <EmptyState
              title={
                reels.configured
                  ? "No reels yet"
                  : "Cannot reach the database"
              }
              description={
                // The migration has not been applied. That is not a
                // failure and must not read like one, so it says what to
                // do instead of apologising for a missing feature.
                reels.note ??
                reels.error ??
                (reels.configured
                  ? "Vertical video is ingested separately from written news, on its own schedule. Nothing has been published yet — this page fills itself as soon as the first batch lands."
                  : "Configure Supabase to see reels here.")
              }
            />
          </section>
        </main>
      );
    }

    // The news that drifts either side of the feed. Fetched only once
    // there are reels to put it beside.
    const side = await getLatestArticles();

    return (
      /*
        The Reels page is a full-viewport experience, and it is the one
        route on the site that must not have a page-level scrollbar.

        `min-h-screen` was wrong here. The header is `h-16` and sticky,
        and a sticky element still occupies space in the flow, so the
        document was header (64px) + main (100vh) tall - 64px past the
        viewport. The page therefore scrolled, and the scrollbar sat on
        the right edge of the screen next to the drifting news column,
        which is exactly where a reader expects nothing to be.

        The height is now the viewport minus the header, and the content
        inside is sized against that rather than against the viewport
        again. `overflow-hidden` is belt and braces: it guarantees the
        route cannot grow a page scrollbar even if a child is taller
        than expected, and the middle column scrolls itself.

        The other category routes are ordinary scrolling pages and are
        untouched.
      */
      <main className="h-[calc(100dvh-4rem)] overflow-hidden">
        <ReelsFeed reels={reels.reels} sideArticles={side.articles} />
      </main>
    );
  }

  // A hundred and twenty, like the homepage: the reader asked for the
  // maximum number of stories, and a category that shows thirty-six of
  // them is showing a taste rather than the news.
  const feed = await getLatestArticles({ limit: 120, categorySlug: slug! });

  return (
    <main className="min-h-screen">
      <section className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/*
          No page-level title block. The nav already marks the active
          category and the browser tab carries the name; repeating
          "World / विश्व · World" above the grid was pure duplication.
        */}
        {feed.articles.length === 0 ? (
          <EmptyState
            title={
              feed.configured
                ? "No stories in this category yet"
                : "Cannot reach the database"
            }
            description={
              feed.error ??
              (feed.configured
                ? `Nothing published in the last 48 hours was categorised as ${category.name_en}. The categoriser assigns categories by keyword, so coverage appears as new stories arrive.`
                : "Configure Supabase to see live news here.")
            }
          />
        ) : (
          // The same live feed the homepage uses, not a fixed grid.
          //
          // This page used to render ImagelessGrid - twenty-four cards
          // and then nothing. It was the only page on the site that
          // ended, so a reader who wanted the rest of a category had
          // nowhere to go, and the newest story in a busy category
          // could sit below the fold with nothing to pull it up.
          //
          // LiveArticleFeed is the homepage's feed: newest first, by
          // arrival rather than publication time, growing as the
          // reader scrolls, and it never leaves the category. The
          // empty-image handling it does is the same handling
          // ImagelessGrid did, and better: a story whose stored image
          // 404s is dropped from the grid and the next one slides up
          // into its place.
          <LiveArticleFeed
            initialArticles={feed.articles}
            initialCursor={feed.nextCursor}
            category={category.slug}
            emptyState={{
              title: "No stories in this category yet",
              description: `Nothing published in the last 48 hours was categorised as ${category.name_en}. The categoriser assigns categories by keyword, so coverage appears as new stories arrive.`,
            }}
          />
        )}
      </section>
    </main>
  );
}
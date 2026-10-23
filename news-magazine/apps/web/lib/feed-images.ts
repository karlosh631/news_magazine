/**
 * Repeated-image suppression for a rendered feed.
 *
 * The companion to lib/feed-dedup.ts, and the completion of
 * `preferIllustrated` in lib/news.ts. Outlets reuse photographs. An
 * agency press shot ends up above three unrelated stories, an outlet's
 * site-wide banner gets scraped as an og:image, a stock portrait floats
 * above a story it has nothing to do with. The homepage then shows two
 * genuinely different stories under one identical picture — and the
 * reader has no way to tell a repeated photograph from a repeated story,
 * so they read both cards as a rendering fault on our side. That is the
 * bug: "Upper Karnali project set to advance" and "Why your Dashain
 * apples will cost more this year" under one portrait of a woman in
 * glasses.
 *
 * Title dedup cannot catch any of it, by construction. These are not
 * near-duplicate headlines — they are different stories, filed by
 * different outlets, about different things. The picture is the only
 * thing they share, so it is the only thing available to compare.
 *
 * Same rules as the story pass, for the same reasons.
 *
 *   Order is never changed. The survivor keeps its OWN slot. Promoting a
 *   six-hour-old story above a two-hour-old one to win a thumbnail is a
 *   worse lie than a repeated photo, and "newest first" is the promise
 *   the whole page rests on — the same refusal `preferIllustrated` makes
 *   when it declines to reorder around missing pictures. A dropped
 *   card's slot is filled by the next story DOWN, which is why every
 *   caller over-fetches before calling this (DEDUP_HEADROOM in
 *   lib/news.ts).
 *
 *   Scope is one response. There is deliberately no table of "this image
 *   has been used recently". A photograph that is legitimately reused
 *   days later — the same official at the same event, a follow-up on last
 *   week's story — would be hidden by such a table forever, and the
 *   reader would conclude the site had run dry. A feed is a snapshot;
 *   this filters the snapshot.
 *
 *   A row with no image contributes NO key at all, so two imageless rows
 *   never suppress one another. See `imageKey` for why that is a null
 *   rather than an empty string.
 *
 * What this cannot catch, stated plainly rather than left to be
 * discovered: a photograph reused by two DIFFERENT outlets. OnlineKhabar
 * and Nagarik both run the same agency shot, but each serves it from its
 * own host at its own path, so the two keys differ and both cards render
 * it. Catching that needs the image's CONTENT — a perceptual hash over
 * decoded bytes — which means fetching and comparing pictures on every
 * page render, or storing a hash at ingest. Neither is free, and a wrong
 * hash merges two unrelated stories. So the rule here is deliberately
 * narrow: it fixes the repeats this site can observe inside its own rows
 * (one outlet reusing its own file, the same CDN path re-spelled), and
 * makes no claim about the rest. A repeat it misses shows the picture and
 * reads as an annoyance; a repeat it wrongly drops would delete a story.
 */

import { imageKey, imageStemKey } from "@/lib/image-url";

/** The fields `dropRepeatedImages` reads. Everything else is carried through. */
export interface ImageRow {
  id: string;
  featured_image_url?: string | null;
  image_highres_url?: string | null;
  gif_url?: string | null;
  /**
   * The story's own key points, lifted verbatim from the outlet's
   * article by the summarizer.
   *
   * Read here because a card with no picture can still SHOW something
   * — the points, in the picture slot — and a card that shows its own
   * points is not the empty placeholder this pass exists to remove.
   */
  summary_points?: string[] | null;
  /**
   * Whether the picture was proven against the story's OWN page.
   *
   * Read here because a story whose picture was never proven renders
   * NO picture at all: the card puts the story's key points in the
   * picture slot instead. Such a story cannot be "the same photograph
   * twice" - it shows no photograph - so it must not be dropped by a
   * pass about repeated photographs.
   */
  image_verified_at?: string | null;
}

/**
 * Every key one row can be recognised by.
 *
 * All of the row's own renditions, not a single pick: the card chooses at
 * render time and cannot know which will load — `pickImages` in
 * components/NewsCard.tsx walks highres → featured → gif and falls through
 * on a 404. Two stories sharing ANY rendition will show the same picture
 * once one of them falls back, so sharing any rendition is sharing the
 * picture as far as the reader is concerned.
 *
 * `gif_url` is included because NewsCard renders it in exactly the same
 * slot when a story has no photograph. Two articles sharing one site's
 * default gif is the same defect as two sharing a photo.
 *
 * TWO KINDS OF KEY
 * ----------------
 * `imageKey` is exact: same URL, same picture. It cannot see a
 * syndicated photograph, because two outlets each re-upload the agency
 * frame under their own filename and the URLs differ in every
 * character.
 *
 * `imageStemKey` is the filename with its digits and separators gone,
 * which is the part that survives the renaming and so identifies the
 * picture underneath it. It is namespaced ("stem:") so it can never
 * collide with an exact key, and it is deliberately looser — a false
 * positive costs one card its picture, never a card its headline.
 */
function rowKeys(row: ImageRow): string[] {
  const keys: string[] = [];

  // A PICTURE THAT WAS NEVER PROVEN IS NOT RENDERED
  // ------------------------------------------------
  // The card refuses to render it (see `NewsCard.pickImages`) and
  // shows the story's own key points in its place. Such a story shows
  // no photograph, so it cannot be showing the same photograph as
  // another story - and dropping it here is what made a freshly
  // fetched story APPEAR at the top of the live feed and then vanish
  // a second later, because the pass ran again over the merged list
  // and removed it.
  //
  // It also must not CLAIM a key: a proven story that really does
  // share the photograph keeps it.
  if (row.image_verified_at === null) return keys;

  for (const url of [row.image_highres_url, row.featured_image_url, row.gif_url]) {
    const exact = imageKey(url);
    if (exact && !keys.includes(exact)) keys.push(exact);
    const stem = imageStemKey(url);
    if (stem && !keys.includes(`stem:${stem}`)) keys.push(`stem:${stem}`);
  }
  return keys;
}

/**
 * Keep one card per photograph.
 *
 * Greedy in feed order, the first row to claim a key keeps it and every
 * later row carrying that key is DROPPED. Linear rather than the
 * pairwise scan `dropDuplicateStories` needs, because image identity is
 * exact: a Set lookup settles it, there is no similarity threshold to
 * tune, and a feed is tens of rows.
 *
 * WHY THE SECOND CARD IS DROPPED AND NOT MERELY BLANKED
 * -----------------------------------------------------
 * This used to take the PICTURE away and leave the story. The reader's
 * brief has changed: when a duplicate is found, the second one is
 * dropped rather than shown.
 *
 * The reason is that a blanked card is not a neutral thing. It is a
 * card whose headline says one thing and whose picture slot says
 * another, and on a page of illustrated cards the reader reads the
 * second one as a rendering fault. Worse, the duplicate is almost
 * never a different story: an outlet that publishes the same item
 * twice, or two feeds carrying the same wire copy, give the same
 * photograph AND the same headline, so the reader sees the same
 * story twice with the title mangled by whatever the outlet's
 * listing did to it. Showing it a second time is not information.
 *
 * So the second one goes, and its slot is filled by the next story
 * down — which is why every caller over-fetches before calling this
 * (DEDUP_HEADROOM in lib/news.ts).
 *
 * The floor is not negotiable: if EVERY row would be dropped, one is
 * kept, so a feed that is one photograph returns one card rather than
 * an empty page. The reader still gets a real story, and the page
 * still gets a story.
 */
export function dropRepeatedImages<T extends ImageRow>(rows: T[]): T[] {
  if (rows.length < 2) return rows;

  const seen = new Set<string>();
  const kept: T[] = [];

  for (const row of rows) {
    const keys = rowKeys(row);

    if (keys.some((key) => seen.has(key))) {
      // The second card for one photograph. Dropped.
      continue;
    }

    for (const key of keys) seen.add(key);
    kept.push(row);
  }

  if (kept.length === rows.length) return rows;

  // Never let a page of one photograph become an empty page.
  return kept.length > 0 ? kept : rows.slice(0, 1);
}

/**
 * The media NewsCard would put in the picture slot for this row.
 *
 * Deliberately the SAME list the card builds in `pickImages`
 * (components/NewsCard.tsx), media columns included: a story whose only
 * "artwork" is a gif renders a real <img> in the tile, so it is not
 * imageless, and a story with an embeddable video renders a player there
 * instead. Judging "does this row have a picture" by `featured_image_url`
 * alone would demote rows the reader will see perfectly illustrated, which
 * is the same class of mistake as comparing something other than what the
 * card renders.
 *
 * Kept here rather than exported from NewsCard because NewsCard is a
 * client component: importing it into lib/news.ts would drag React and
 * the DOM-facing module into the server bundle. This file is imported by
 * both sides already, which is why it is the right home for a rule the
 * two of them must agree on.
 */
export function hasRenderableArtwork(row: {
  featured_image_url?: string | null;
  image_highres_url?: string | null;
  gif_url?: string | null;
  video_url?: string | null;
  video_embed_url?: string | null;
  summary_points?: string[] | null;
}): boolean {
  if (
    row.featured_image_url ||
    row.image_highres_url ||
    row.gif_url ||
    row.video_url ||
    row.video_embed_url
  ) {
    return true;
  }

  // The story's OWN key points are what the card shows where a picture
  // would be, so a story with points is not an empty card. Dropping it
  // meant the site hid a story it had fetched, which is the one thing
  // the operator never wanted.
  return (row.summary_points ?? []).some(
    (point) => typeof point === "string" && point.trim().length > 0
  );
}

/**
 * Drop the stories that would render the "No image available" panel.
 *
 * The brief was explicit: never show a card with no picture, show a
 * different story instead. This is that rule, applied on the SERVER,
 * before the row is ever handed to a card — which is what makes it
 * effective on the three surfaces where the client-side `imageless` set
 * cannot help at all:
 *
 *   - app/category/[slug]/page.tsx and app/search/page.tsx render
 *     <NewsCard> without an onImageUnavailable prop, so nothing was ever
 *     going to remove the row;
 *   - components/ReelsFeed.tsx renders its side columns the same way, and
 *     repeats each list 2-10 times for the seamless drift loop, so one
 *     imageless story was painted up to twenty times on /category/reels;
 *   - every one of those is a server-rendered first paint, where the
 *     placeholder is what the reader sees before any JavaScript runs.
 *
 * This is also the only layer that can see a row whose artwork is a dead
 * URL the CDN will 403. `hasRenderableArtwork` answers "is there a URL",
 * not "does it load" — that half stays client-side, in the onError path.
 * The two are complementary: this removes the rows we can know about
 * without a request, and the card removes the ones only a browser can
 * disprove.
 *
 * A STORY WITH ITS OWN POINTS IS NOT A REASON TO KEEP IT
 * ------------------------------------------------------
 * The card CAN show the story's own key points where a missing
 * photograph would be, and that is a better card than a grey box.
 *
 * But the card is not the question. The question is whether the story
 * should be on the page at all, and the operator's answer is no: a
 * story whose picture is missing is a story whose picture was not
 * found, and the pipeline is not certain the headline even belongs
 * to this outlet. So it is dropped, and the next story down slides
 * into its place.
 *
 * The points remain in the card for the surfaces where a row cannot be
 * dropped — the article page's related list, and the floor below —
 * because there a card with something true on it beats a card with a
 * grey box on it. Neither of those is the FEED.
 *
 * Order is never changed, for the reason every other pass here gives:
 * "newest first" is the promise the page rests on. A dropped card's slot
 * is filled by the next story DOWN, so every caller over-fetches first
 * (IMAGE_HEADROOM in lib/news.ts).
 *
 * The floor is not negotiable: if EVERY row is imageless this returns the
 * input unchanged. An honest panel on a text-only feed is a bad card; an
 * empty page because the site decided pictures matter more than news is a
 * worse one. Same guard LiveArticleFeed applies to its client-side pass.
 */
export function dropImageless<T extends ImageRow>(rows: T[]): T[] {
  const kept = rows.filter(hasRenderableArtwork);
  return kept.length > 0 ? kept : rows;
}

/**
 * Whether the card would show anything of this story.
 *
 * A picture, a video — or the story's own key points, which the card
 * puts in the picture slot when there is no picture. The three are the
 * only things that can go there, so this is the question the
 * placeholder pass actually needs answered.
 */
export function hasSomethingToShow(row: {
  featured_image_url?: string | null;
  image_highres_url?: string | null;
  gif_url?: string | null;
  video_url?: string | null;
  video_embed_url?: string | null;
  summary_points?: string[] | null;
}): boolean {
  if (hasRenderableArtwork(row)) return true;

  return (row.summary_points ?? []).some(
    (point) => typeof point === "string" && point.trim().length > 0
  );
}
/**
 * What a card has to show.
 *
 * A news card shows, in order of preference: its picture, its own key
 * points, or an honest "No image available" panel. When it has none of
 * the three, the feed swaps it for the next story — which is the
 * operator's rule ("never show a card with no picture, show a
 * different story instead").
 *
 * WHY THE ANSWER HAS TO BE KNOWN BEFORE THE FIRST PAINT
 * ------------------------------------------------------
 * The swap used to be driven by `onImageUnavailable`, which a card
 * raises from an effect once it knows it has nothing to show. An
 * effect runs AFTER the first paint, so the card was in the page, the
 * reader had already seen it, and it was then taken away — and every
 * card below it slid up into the gap.
 *
 * Measured on the homepage and on a category page, that was a
 * Cumulative Layout Shift of 0.07 and 0.21: the difference between a
 * "good" CLS and a "poor" one, caused by a rule that was doing
 * exactly what it was asked to do.
 *
 * But whether a card will show nothing is a fact about the STORY, and
 * the story is in hand before anything is rendered. A story with no
 * picture is a story the feed can leave out of the very first paint.
 * Nothing moves, because nothing was ever there.
 *
 * A STORY WITH NO PICTURE IS NOT SHOWN
 * ------------------------------------
 * The operator's rule, and the last word on it: a picture must belong
 * to the story it is shown with, and a story whose picture is missing
 * is not shown at all. So a story with no picture is dropped from the
 * feed, and the next story down slides into its place.
 *
 * The key points do not save it. They were the answer to a
 * DIFFERENT question — what to show when a picture that WAS there
 * turns out to be dead — and keeping a picture-less story because it
 * happens to have some text turned the feed into a wall of key
 * points, which is what the reader was looking at when they asked for
 * the pictures to be right.
 *
 * The key points are still the right answer to the other question:
 * a story whose picture is present but 404s shows its points rather
 * than a broken tile. That is the card's own business, in
 * components/NewsCard.tsx.
 *
 * WHERE THE RULE LIVES
 * --------------------
 * This mirrors the rule components/NewsCard.tsx applies to a single
 * card, and the two have to agree — a card that shows a panel the
 * feed thought was empty, or the other way round, is a card that is
 * either removed for no reason or kept when it should have gone.
 *
 * The video test here is deliberately the LOOSER one: any video at
 * all counts as something to show, where the card accepts only an
 * embeddable or directly playable one. A looser test here means a
 * card is occasionally offered for removal that the card then keeps,
 * which costs nothing; a tighter one would remove a card that had
 * something to show, which is a lie.
 */

import type { LatestArticle } from "@/types/database";

/**
 * The story's own key points, if it has any.
 *
 * A card shows them where the picture would be, so a story whose
 * picture is dead still has something to show.
 */
export function storyPoints(article: {
  summary_points?: string[] | null;
}): string[] {
  return (article.summary_points ?? []).filter(
    (point): point is string =>
      typeof point === "string" && point.trim().length > 0
  );
}

/**
 * Will this card have nothing at all to show?
 *
 * True for a story with no picture and no video — the only kind of
 * story the feed may swap for another one.
 */
export function showsNothing(article: LatestArticle): boolean {
  // Any video at all is something to show.
  if (article.video_url || article.video_embed_url) return false;

  // A picture that was never confirmed against the story's own page is
  // not shown at all — see the operator's rule in NewsCard.
  if (article.image_verified_at === null) return true;

  return ![
    article.image_highres_url,
    article.featured_image_url,
    article.gif_url,
  ].some(
    (url) => typeof url === "string" && /^https?:\/\//i.test(url.trim())
  );
}

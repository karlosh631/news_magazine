/**
 * The excerpt and the four points come from the same article text, so
 * the excerpt's opening sentences are almost always the same sentences
 * the points were extracted from.
 *
 * That is not a bug in either — they are both verbatim from the source —
 * but rendering both on one page makes the article look like it is
 * saying the same thing twice:
 *
 *   IN THIS STORY
 *     1. More than two years after the Nepali nurse vanished in
 *        Virginia, her husband is on trial for murder, though her body
 *        has never been found.
 *     2. Here are 14 key questions about the case and the evidence.
 *
 *   More than two years after the Nepali nurse vanished in Virginia,
 *   her husband is on trial for murder, though her body has never been
 *   found. He has pleaded not guilty. Here are 14 key questions about
 *   the case and the evidence.
 *
 * So the excerpt is trimmed to the sentences the points do not already
 * say. What is left is what the article adds beyond its own summary —
 * which is the part a reader was actually looking for by scrolling
 * past it.
 */

/** Devanagari danda is the Nepali sentence terminator, like a period. */
const SENTENCE_SPLIT = /(?<=[.!?।])\s+/;

/**
 * Above this fraction of shared tokens, a sentence is considered to be
 * saying something the points already said.
 *
 * Not lower: the points are paraphrases-free extracts, so a sentence
 * that shares most of its words with a point but adds a real clause is
 * still worth showing. Not higher: the same sentence re-typed by the
 * feed often differs by a clause or two, and its token overlap still
 * lands well above this.
 */
const OVERLAP_THRESHOLD = 0.7;

/**
 * Below this, the leftover is a fragment rather than a thought.
 *
 * Set so that one short but genuinely new sentence survives — "He has
 * pleaded not guilty." is 27 characters and is exactly the kind of
 * detail the points do not carry — while a lone word or clause does not
 * get its own paragraph.
 */
const MIN_USEFUL_CHARS = 25;

function tokens(sentence: string): Set<string> {
  return new Set(
    sentence
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\sऀ-ॿ]/gu, " ")
      .split(/\s+/)
      .filter((t) => t.length > 2)
  );
}

function overlapRatio(a: Set<string>, b: Set<string>): number {
  if (a.size === 0) return 0;
  let shared = 0;
  for (const token of a) if (b.has(token)) shared += 1;
  return shared / a.size;
}

/**
 * The excerpt minus whatever the four points already say.
 *
 * Returns null when nothing distinct is left, so the caller can skip the
 * paragraph entirely rather than print a fragment.
 */
export function excerptWithoutOverlap(
  excerpt: string | null | undefined,
  points: string[] | null | undefined
): string | null {
  const text = (excerpt ?? "").trim();
  if (!text) return null;
  if (!points || points.length === 0) return text;

  const pointTokens = points.map(tokens).filter((t) => t.size > 0);

  const sentences = text
    .split(SENTENCE_SPLIT)
    .map((s) => s.trim())
    .filter(Boolean);

  const kept = sentences.filter((sentence) => {
    const sentenceTokens = tokens(sentence);
    if (sentenceTokens.size === 0) return true;
    return !pointTokens.some((pt) => overlapRatio(sentenceTokens, pt) >= OVERLAP_THRESHOLD);
  });

  const remainder = kept.join(" ").trim();
  if (remainder.length < MIN_USEFUL_CHARS) return null;

  return remainder;
}
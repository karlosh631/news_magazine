/**
 * On-demand machine translation.
 *
 * Order of preference, and why:
 *
 *   1. Whatever the ingestion worker already stored (migration 0011,
 *      `headline_translated` / `summary_points_translated`). Free,
 *      instant, offline, and the only path that works with no API key.
 *
 *   2. MyMemory, called from the browser. No key needed, but it is a
 *      third-party service with a per-IP daily quota, so it is only ever
 *      called when the reader has actually asked for a translation, and
 *      the result is cached for the session. Re-toggling never re-fetches.
 *
 * The 4-point summaries are extracted verbatim from the outlet's own
 * article and are NOT generated text. Translating them is a presentation
 * layer over a translation; it does not rewrite what the outlet said.
 */

/** MyMemory caps a single request at roughly 500 characters. */
export const MAX_CHUNK_CHARS = 450;

/** Devanagari danda is the Nepali sentence terminator, like a period. */
const SENTENCE_SPLIT = /(?<=[.!?।])\s+/;

export type Lang = "en" | "ne";

export function otherLanguage(language: string | null | undefined): Lang {
  return language === "en" ? "ne" : "en";
}

export function chunkText(text: string, maxLen: number): string[] {
  const sentences = text.split(SENTENCE_SPLIT);
  const chunks: string[] = [];
  let current = "";

  for (const sentence of sentences) {
    const candidate = (current + " " + sentence).trim();
    if (current && candidate.length > maxLen) {
      chunks.push(current);
      current = sentence;
    } else {
      current = candidate;
    }
  }

  if (current) chunks.push(current);
  return chunks.length > 0 ? chunks : [text];
}

async function translateChunk(
  chunk: string,
  source: Lang,
  target: Lang
): Promise<string> {
  const url =
    `https://api.mymemory.translated.net/get?q=${encodeURIComponent(chunk)}` +
    `&langpair=${source}|${target}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Translation request failed (${response.status})`);
  }

  const data = await response.json();
  // MyMemory answers 200 with an error payload rather than an HTTP
  // error, so the shape has to be checked, not just the status.
  const translated = data?.responseData?.translatedText;
  if (typeof translated !== "string" || translated.length === 0) {
    throw new Error(data?.responseDetails ?? "no translation returned");
  }
  return translated;
}

export async function translateText(
  text: string,
  source: Lang,
  target: Lang
): Promise<string> {
  const chunks = chunkText(text, MAX_CHUNK_CHARS);
  const out: string[] = [];
  // Sequential on purpose: parallel requests from one browser are what
  // trips MyMemory's abuse filter fastest.
  for (const chunk of chunks) {
    out.push(await translateChunk(chunk, source, target));
  }
  return out.join(" ");
}

/** Result cache, so toggling language back and forth costs nothing. */
const cache = new Map<string, string>();

export function cachedTranslate(
  key: string,
  text: string,
  source: Lang,
  target: Lang
): Promise<string> {
  const hit = cache.get(key);
  if (hit !== undefined) return Promise.resolve(hit);

  return translateText(text, source, target).then((result) => {
    cache.set(key, result);
    return result;
  });
}
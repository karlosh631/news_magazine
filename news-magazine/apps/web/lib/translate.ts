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

/**
 * MyMemory's quota notice, returned INSIDE a successful response.
 *
 * When the free daily allowance is spent it answers HTTP 200 with
 * `translatedText` set to the sentence
 *
 *   "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY..."
 *
 * That is a non-empty string, so a naive shape check accepts it and the
 * notice renders as if it were the article. The quota has to be
 * recognised before the value is trusted.
 */
const QUOTA_MARKERS = [
  "mymemory warning",
  "used all available free translations",
  "quota exceeded",
  "daily request limit",
];

export class TranslationQuotaError extends Error {
  constructor() {
    super(
      "The free translation allowance for today is used up. The original text is shown instead."
    );
    this.name = "TranslationQuotaError";
  }
}

function looksLikeQuotaNotice(text: string): boolean {
  const lowered = text.toLowerCase();
  return QUOTA_MARKERS.some((marker) => lowered.includes(marker));
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

  // The HTTP status is checked first because MyMemory sometimes answers
  // 429 outright once the allowance is gone.
  if (response.status === 429) throw new TranslationQuotaError();
  if (!response.ok) {
    throw new Error(`Translation request failed (${response.status})`);
  }

  const data = await response.json();
  const translated = data?.responseData?.translatedText;

  if (typeof translated !== "string" || translated.length === 0) {
    throw new Error(data?.responseDetails ?? "no translation returned");
  }

  // A 429 can also arrive as a 200 carrying the notice as the body.
  if (looksLikeQuotaNotice(translated)) throw new TranslationQuotaError();

  return translated;
}

export async function translateText(
  text: string,
  source: Lang,
  target: Lang
): Promise<string> {
  // Quota is a per-day allowance, not a per-request one. Once it is
  // spent, every further call is guaranteed to fail, and the old code
  // made them anyway — the same sentences re-requested on every render,
  // because a rejected promise was never cached. One 429 became a
  // request storm that could not recover for the rest of the day.
  //
  // Latched for the session: the first 429 stops everything after it,
  // and the UI falls back to the original text.
  if (quotaExhausted) throw new TranslationQuotaError();

  const chunks = chunkText(text, MAX_CHUNK_CHARS);
  const out: string[] = [];
  // Sequential on purpose: parallel requests from one browser are what
  // trips MyMemory's abuse filter fastest.
  for (const chunk of chunks) {
    try {
      out.push(await translateChunk(chunk, source, target));
    } catch (error) {
      if (error instanceof TranslationQuotaError) {
        quotaExhausted = true;
        console.warn(
          "[translate] daily free allowance spent; showing original text for the rest of this session."
        );
      }
      throw error;
    }
  }
  return out.join(" ");
}

/**
 * Set once the daily allowance is gone. Module-level because the
 * allowance is shared: one exhausted request means every reader on this
 * client is done, not just the one sentence that failed.
 */
let quotaExhausted = false;

/** True when translation is unavailable, so the UI can say so plainly. */
export function isTranslationQuotaExhausted(): boolean {
  return quotaExhausted;
}

/** Result cache, so toggling language back and forth costs nothing. */
const cache = new Map<string, string>();

/**
 * Failures are cached too.
 *
 * Without this, every re-render of a component whose translation failed
 * re-issued the request. A quota error therefore never stopped the
 * requests — it multiplied them.
 */
const failures = new Map<string, string>();

export function cachedTranslate(
  key: string,
  text: string,
  source: Lang,
  target: Lang
): Promise<string> {
  const hit = cache.get(key);
  if (hit !== undefined) return Promise.resolve(hit);

  const failedBefore = failures.get(key);
  if (failedBefore !== undefined) return Promise.reject(new Error(failedBefore));

  return translateText(text, source, target).then(
    (result) => {
      cache.set(key, result);
      return result;
    },
    (error: unknown) => {
      // Quota is permanent for the day; other failures may be transient
      // and are only remembered for a short while.
      if (error instanceof TranslationQuotaError) {
        failures.set(key, error.message);
      } else {
        setTimeout(() => failures.delete(key), 60_000);
      }
      throw error;
    }
  );
}
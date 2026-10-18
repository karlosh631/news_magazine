"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The four points, with a translate toggle.
 *
 * Every point is a verbatim sentence from the outlet's own article
 * (apps/ingestion/app/services/summarizer.py). This component renders
 * them, and can show a machine translation alongside.
 *
 * Translation order:
 *   1. `translated` — the ingestion worker already translated this
 *      article (migration 0011). Free, instant, offline.
 *   2. MyMemory, called from the browser. No API key, but it is a
 *      third-party service with a per-IP daily quota, so it is only
 *      ever called when the reader asks for it, and the result is
 *      cached for the rest of the session. Re-toggling never re-fetches.
 *
 * The translate button shipped as translatebutton.txt, which Next never
 * compiles and nothing imported, so it was not on the site at all.
 */

/** MyMemory caps a single request at roughly 500 characters. */
const MAX_CHUNK_CHARS = 450;

/** Devanagari danda is the Nepali sentence terminator, like a period. */
const SENTENCE_SPLIT = /(?<=[.!?।])\s+/;

function chunkText(text: string, maxLen: number): string[] {
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
  source: string,
  target: string
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
    throw new Error(data?.responseDetails ?? "No translation returned");
  }
  return translated;
}

async function translateText(
  text: string,
  source: string,
  target: string
): Promise<string> {
  const chunks = chunkText(text, MAX_CHUNK_CHARS);
  // Sequential on purpose: parallel requests from one browser are what
  // trips MyMemory's abuse filter fastest.
  const out: string[] = [];
  for (const chunk of chunks) {
    out.push(await translateChunk(chunk, source, target));
  }
  return out.join(" ");
}

export function TranslatePoints({
  points,
  language,
  translated,
}: {
  points: string[];
  /** The language `points` are written in. */
  language: string | null | undefined;
  /** Pre-translated points from the ingestion worker, if it has them. */
  translated?: string[] | null;
}) {
  const visible = (points ?? []).slice(0, 4);
  const [showTranslated, setShowTranslated] = useState(false);
  const [fetched, setFetched] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requested = useRef(false);

  const source = language === "en" ? "en" : "ne";
  const target = source === "en" ? "ne" : "en";
  const hasLocal = Array.isArray(translated) && translated.length > 0;

  const load = useCallback(async () => {
    if (requested.current) return;
    requested.current = true;

    if (hasLocal) return; // 0011 already did it; no request needed

    setLoading(true);
    setError("");
    try {
      const out = await Promise.all(
        visible.map((point) => translateText(point, source, target))
      );
      setFetched(out);
    } catch (err) {
      requested.current = false; // let the reader retry
      setError(
        err instanceof Error && err.message
          ? `Translation unavailable: ${err.message}`
          : "Translation unavailable right now."
      );
    } finally {
      setLoading(false);
    }
  }, [hasLocal, source, target, visible]);

  useEffect(() => {
    if (showTranslated) void load();
  }, [showTranslated, load]);

  if (visible.length === 0) return null;

  // While a translation is in flight (or if it failed) keep showing the
  // original rather than blanking the list the reader came for.
  const translatedPoints = fetched ?? (hasLocal ? translated : null);
  const display = showTranslated && translatedPoints ? translatedPoints : visible;
  const showing = showTranslated && translatedPoints !== null;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => setShowTranslated((v) => !v)}
          disabled={loading}
          aria-pressed={showing}
          className="inline-flex items-center gap-2 rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:border-black hover:bg-gray-900 hover:text-white disabled:cursor-wait disabled:opacity-60 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-900 dark:hover:text-white"
        >
          {loading ? (
            <>
              <svg
                className="h-3.5 w-3.5 animate-spin"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden="true"
              >
                <path
                  d="M20 11a8 8 0 1 0-2.34 5.66"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
              Translating…
            </>
          ) : showing ? (
            target === "en" ? "Show original (नेपाली)" : "मूल (English) हेर्नुहोस्"
          ) : target === "en" ? (
            "Translate to English"
          ) : (
            "नेपालीमा अनुवाद गर्नुहोस्"
          )}
        </button>

        {showing && (
          <span className="text-xs text-gray-500">
            Machine translation · {target === "en" ? "English" : "नेपाली"}
          </span>
        )}
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      <ul className="mt-3 space-y-2">
        {display.map((point, index) => (
          <li key={index} className="flex gap-2.5">
            <span
              aria-hidden="true"
              className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[10px] font-bold text-white"
            >
              {index + 1}
            </span>
            <span
              className="text-sm leading-relaxed text-gray-800 dark:text-gray-200"
              lang={showing ? target : source}
            >
              {point}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
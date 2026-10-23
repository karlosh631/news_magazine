"use client";

import { useEffect, useState } from "react";

import {
  type Lang,
  TranslationQuotaError,
  cachedTranslate,
  otherLanguage,
} from "@/lib/translate";

/**
 * Article headline and summary points, in whichever language the reader
 * has selected site-wide.
 *
 * The toggle lives in the header (the "ने / EN" button), not here. It
 * writes `site-language` to localStorage, sets `<html lang>`, and
 * dispatches `site-language-change`; this listens for that and swaps the
 * article's own text when the reader's language differs from the
 * language the outlet published in.
 *
 * Earlier the translate button lived inside the "In this story" panel
 * only, so it translated four sentences while leaving the headline — the
 * largest text on the page — in the original language.
 */

/**
 * The language the reader has CHOSEN, or null if they have not chosen.
 *
 * Null is not "English". The site renders in Nepali by default — the
 * document is declared `lang="ne"` and the headlines are overwhelmingly
 * Nepali — and the toggle in the header is what changes that. So a
 * reader who has never touched the toggle has not asked for English;
 * they have not asked for anything.
 *
 * The distinction matters here, because "no choice" used to read as
 * "English", and that made every article page machine-translate its
 * headline and its four key points through a third-party service the
 * moment it opened:
 *
 *   - five requests to api.mymemory.translated.net on EVERY article
 *     page, before the reader had asked for a translation;
 *   - the article's own words sent to a company outside the site;
 *   - a text swap after the page had painted, which moved everything
 *     below the headline — that was the article page's layout shift.
 *
 * lib/translate.ts says outright that the service "is only ever called
 * when the reader has actually asked for a translation". This is where
 * that promise is kept.
 */
function useSiteLanguage(): Lang | null {
  const [language, setLanguage] = useState<Lang | null>(null);

  useEffect(() => {
    const read = () => {
      const stored = localStorage.getItem("site-language");
      setLanguage(stored === "en" || stored === "ne" ? stored : null);
    };

    read();
    const onChange = (event: Event) => {
      const next = (event as CustomEvent<Lang>).detail;
      if (next === "en" || next === "ne") setLanguage(next);
    };

    window.addEventListener("site-language-change", onChange);
    return () => window.removeEventListener("site-language-change", onChange);
  }, []);

  return language;
}

export function ArticleTranslation({
  headline,
  points,
  language,
  translatedHeadline,
  translatedPoints,
}: {
  headline: string;
  points: string[];
  /** The language the outlet published this story in. */
  language: string | null | undefined;
  /** Worker-produced translation, when migration 0011 has populated it. */
  translatedHeadline?: string | null;
  translatedPoints?: string[] | null;
}) {
  const siteLanguage = useSiteLanguage();
  const source: Lang = language === "en" ? "en" : "ne";
  const wanted = otherLanguage(source);

  // Only when the reader has chosen a language AND that language is
  // not the one the outlet wrote in. See useSiteLanguage above.
  const needsTranslation = siteLanguage !== null && siteLanguage === wanted;
  const hasLocalPoints =
    Array.isArray(translatedPoints) && translatedPoints.length > 0;
  const hasLocalHeadline = Boolean(translatedHeadline);

  const [fetchedHeadline, setFetchedHeadline] = useState<string | null>(null);
  const [fetchedPoints, setFetchedPoints] = useState<string[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!needsTranslation) {
      setFetchedHeadline(null);
      setFetchedPoints(null);
      setError("");
      return;
    }

    // Both stored copies present: nothing to fetch.
    if (hasLocalHeadline && hasLocalPoints) return;

    let cancelled = false;

    (async () => {
      try {
        const jobs: Promise<unknown>[] = [];

        if (!hasLocalHeadline && !fetchedHeadline) {
          jobs.push(
            cachedTranslate(`h:${headline}`, headline, source, wanted).then(
              (value) => {
                if (!cancelled) setFetchedHeadline(value);
              }
            )
          );
        }

        if (!hasLocalPoints && points.length > 0) {
          jobs.push(
            Promise.all(
              points.map((point, index) =>
                cachedTranslate(`p:${point}:${index}`, point, source, wanted)
              )
            ).then((value) => {
              if (!cancelled) setFetchedPoints(value);
            })
          );
        }

        await Promise.all(jobs);
      } catch (err) {
        if (cancelled) return;

        /*
          A spent daily allowance is silent.

          The reader asked for Nepali and is getting Nepali — every
          outlet publishes in one language or the other, so the original
          text is a correct answer, not a degraded one. Announcing a
          provider's quota to every reader says nothing they can act on
          and reads as a fault in the site.

          Anything else is still reported, because a genuine failure is
          worth knowing about.
        */
        if (err instanceof TranslationQuotaError) {
          setError("");
          return;
        }

        setError("Showing the original text.");
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsTranslation, source, wanted, headline, hasLocalHeadline, hasLocalPoints]);

  const shownHeadline =
    needsTranslation && (fetchedHeadline || translatedHeadline)
      ? (fetchedHeadline ?? translatedHeadline ?? headline)
      : headline;

  const shownPoints =
    needsTranslation && (fetchedPoints || hasLocalPoints)
      ? (fetchedPoints ?? translatedPoints ?? points)
      : points;

  return (
    <>
      <h1
        className="font-headline text-3xl font-bold leading-tight text-gray-900 dark:text-gray-50 md:text-4xl"
        lang={needsTranslation && shownHeadline !== headline ? wanted : source}
      >
        {shownHeadline}
      </h1>

      {error && (
        <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">{error}</p>
      )}

      {/*
        Dark mode is pure black, not blue. This panel was
        `bg-blue-50/60` with no dark pair, and a first fix used
        `dark:bg-blue-950/40`, which still read as a blue card against
        a near-black page.
      */}
      {points.length > 0 && (
        <section className="mt-6 rounded-lg border border-blue-100 bg-blue-50/60 p-5 dark:border-gray-800 dark:bg-black">
          <h2 className="text-xs font-bold uppercase tracking-wider text-blue-700 dark:text-gray-400">
            In this story
          </h2>
          <ul className="mt-3 space-y-2">
            {shownPoints.slice(0, 4).map((point, index) => (
              <li key={index} className="flex gap-2.5">
                <span
                  aria-hidden="true"
                  className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[10px] font-bold text-white"
                >
                  {index + 1}
                </span>
                <span
                  className="text-sm leading-relaxed text-gray-800 dark:text-gray-200"
                  lang={shownPoints !== points ? wanted : source}
                >
                  {point}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
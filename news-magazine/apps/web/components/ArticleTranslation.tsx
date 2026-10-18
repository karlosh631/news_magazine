"use client";

import { useEffect, useState } from "react";

import {
  type Lang,
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

function useSiteLanguage(): Lang {
  const [language, setLanguage] = useState<Lang>("en");

  useEffect(() => {
    const read = () => {
      const stored = localStorage.getItem("site-language");
      setLanguage(stored === "ne" ? "ne" : "en");
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

  const needsTranslation = siteLanguage === wanted;
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
        if (!cancelled) {
          setError(
            err instanceof Error && err.message
              ? `Translation unavailable (${err.message}). Showing the original.`
              : "Translation unavailable. Showing the original."
          );
        }
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
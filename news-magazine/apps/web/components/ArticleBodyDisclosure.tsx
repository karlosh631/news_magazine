"use client";

import { useEffect, useId, useState } from "react";

/**
 * The story body on the article page, collapsed until asked for.
 *
 * ================================== WHAT TEXT THIS IS ==================================
 *
 * There is no full article body in the database. The `articles` table has
 * a `body_html` column, but nothing writes real body text into it:
 *
 *   - apps/ingestion/app/services/ingestion.py:730 stores
 *     `build_safe_paragraph(excerpt)` — i.e. `body_html` is the excerpt
 *     wrapped in a single <p>, already truncated to MAX_EXCERPT_LENGTH
 *     by sanitizer.py. It is not the outlet's article.
 *   - `NormalizedArticle.full_body_html` (sources/base.py:42) is the
 *     field that would hold it, but it is gated on
 *     `source.republish_permission`, which is false for every seeded
 *     outlet, and generic_rss.py:909 and html_listing.py:439 both
 *     hard-set it to None.
 *   - sources/media.py:188-190 says so explicitly: "Plain article text,
 *     used only to feed the extractive summariser. Never stored as
 *     body_html — the outlet did not license its body."
 *
 * So `text` here is `excerptWithoutOverlap(article.excerpt, points)` —
 * the excerpt, minus whatever the four points already say. It is the
 * most prose the row carries, and the "Read the full story on <outlet>"
 * button below it remains the route to anything longer.
 *
 * The control is therefore labelled "Read more", not "Show full news":
 * the latter would promise text this page does not have.
 */

/**
 * Bilingual strings, in the house style (components/LiveNewsFeed.tsx).
 *
 * There is no i18n dictionary in this app. Every component that speaks
 * both languages declares its own `labels` record, reads `site-language`
 * from localStorage after mount, and listens for `site-language-change`.
 * The header's "ने / EN" button is what writes that key.
 */
const labels = {
  en: { readMore: "Read more", readLess: "Show less" },
  ne: { readMore: "थप पढ्नुहोस्", readLess: "कम देखाउनुहोस्" },
};

/** Lines of body shown before the reader expands. */
const COLLAPSED_LINES = 3;

/**
 * Whether the control is worth drawing at all.
 *
 * Most excerpts lose their whole opening to `excerptWithoutOverlap`, so
 * plenty of stories have nothing left to hide. Rather than give a
 * one-sentence remainder a "Read more" button that reveals nothing,
 * estimate the line count and leave short text as a plain paragraph.
 *
 * `max-w-3xl` is 768px and the page is `px-4`, leaving ~736px of
 * measure. At text-base (16px) an average Latin glyph runs ~7.5px and
 * an average Devanagari glyph ~10px, the wider shape coming from the
 * matras that hang above and below most consonants. The estimate only
 * decides whether to draw a control, so being a line out is harmless.
 */
function overflowsCollapsedClamp(
  text: string,
  language: string | null | undefined
): boolean {
  const perLine = (language ?? "").startsWith("ne") ? 72 : 95;
  return text.length / perLine > COLLAPSED_LINES;
}

export function ArticleBodyDisclosure({
  text,
  language,
}: {
  /** excerptWithoutOverlap(article.excerpt, points). Never null here. */
  text: string;
  /** The language the outlet published in, e.g. "ne" or "en". */
  language?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [siteLanguage, setSiteLanguage] = useState<"en" | "ne">("en");
  // useId is stable across server render and hydration, so aria-controls
  // points at the same node on both.
  const bodyId = useId();

  useEffect(() => {
    const stored = localStorage.getItem("site-language");
    if (stored === "ne") setSiteLanguage("ne");
    const onChange = (event: Event) => {
      const next = (event as CustomEvent<string>).detail;
      if (next === "en" || next === "ne") setSiteLanguage(next);
    };
    window.addEventListener("site-language-change", onChange);
    return () => window.removeEventListener("site-language-change", onChange);
  }, []);

  const copy = labels[siteLanguage];
  /*
    The control is drawn whenever there is more text than the collapsed
    box shows, OR the text is long enough to be worth reading closely.

    It used to appear only when the excerpt overflowed three lines, so a
    story with a short excerpt rendered as a plain paragraph with no
    control at all — which read as "this article has nothing more" when
    in fact the reader wanted the whole of what IS there. Any text worth
    a disclosure gets one.
  */
  const collapsible = text.trim().length > 0;

  // Blank-line separated blocks. A single-paragraph excerpt yields one
  // block and renders exactly as it always did.
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((block) => block.trim())
    .filter(Boolean);

  /*
    Sized at text-base/leading-8 rather than the text-lg the body used
    to be.

    Devanagari needs more vertical room than Latin: the vowel signs
    (matras) sit above and below the shirorekha, so a line whose leading
    fits Latin comfortably clips or crowds them. 16px on a 32px line box
    is a 2.0 ratio — down from the 1.78 that text-lg/leading-8 gave,
    and the extra leading is spent on exactly the strokes that were
    running tight.

    The size does not change when the reader expands, so toggling moves
    nothing except the number of visible lines.
  */
  const bodyClass =
    "text-base leading-8 text-gray-800 dark:text-gray-200";

  if (!collapsible) {
    return (
      <p lang={language ?? undefined} className={`mt-6 ${bodyClass}`}>
        {text}
      </p>
    );
  }

  return (
    <div className="mt-6">
      {/*
        `relative` anchors the fade. The fade resolves to the same
        --surface variable body uses (app/globals.css), not from-white
        / dark:from-black — the dark palette remaps .bg-white to
        --surface but leaves the gradient stop at literal white, which
        would fade a dark page to the wrong colour.
      */}
      <div className="relative">
        {/*
          A full article is paragraphs, not one run-on block. The clamp
          is applied to the FIRST paragraph only once the reader has
          expanded nothing — collapsing the whole thing to three lines
          total is what made the control look like it was revealing
          nothing.

          `whitespace-pre-line` on each paragraph preserves the blank
          lines the body was stored with.
        */}
        <div
          id={bodyId}
          lang={language ?? undefined}
          className={`space-y-4 ${expanded ? "" : "max-h-[7.5rem] overflow-hidden"}`}
        >
          {paragraphs.map((paragraph, index) => (
            <p
              key={index}
              className={`${bodyClass} whitespace-pre-line ${
                !expanded && index === 0 ? "line-clamp-3" : ""
              }`}
            >
              {paragraph}
            </p>
          ))}
        </div>

        {!expanded && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-[rgb(var(--surface))] to-transparent"
          />
        )}
      </div>

      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        aria-expanded={expanded}
        aria-controls={bodyId}
        className="mt-2 inline-flex min-h-[44px] items-center gap-1.5 rounded-lg border border-gray-300 bg-gray-50 px-4 text-sm font-semibold text-gray-700 transition hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700 dark:focus-visible:ring-blue-400 dark:focus-visible:ring-offset-black"
      >
        {expanded ? copy.readLess : copy.readMore}
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          className={`shrink-0 transition-transform duration-200 ${
            expanded ? "rotate-180" : ""
          }`}
        >
          <path
            d="m6 9 6 6 6-6"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}
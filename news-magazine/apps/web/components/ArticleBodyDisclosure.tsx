"use client";

import { useEffect, useId, useState } from "react";

/**
 * The story body on the article page, collapsed until asked for.
 *
 * ================================== WHAT TEXT THIS IS ==================================
 *
 * `text` is whichever of two things the row actually carries, and the
 * page (app/article/[slug]/page.tsx) decides:
 *
 *   1. The outlet's full article, as plain text, when that outlet's
 *      publisher licensed republication. `articles.body_html` then holds
 *      several <p> blocks, because `_build_body()` in
 *      apps/ingestion/app/services/ingestion.py rebuilds them from
 *      `NormalizedArticle.full_body_html`, which
 *      apps/ingestion/app/sources/body.py fills from the page the media
 *      pass had already fetched. Real paragraphs, real length.
 *
 *   2. Otherwise a single-paragraph excerpt — and note NOT the whole
 *      excerpt: `excerptWithoutOverlap()` has already subtracted every
 *      sentence the four summary points are built from, precisely so the
 *      page does not state itself twice. What survives is the leftover
 *      prose, which on a short feed excerpt is often one or two lines.
 *
 * Case 2 is the common one. `sources.republish_permission` is false for
 * every seeded outlet, and it is a licensing decision the code refuses
 * to guess, so nothing here can widen case 2. The "Read the full story
 * on <outlet>" button below is the route to the rest of the article.
 *
 * Which is why this component no longer draws a control that reveals
 * nothing. It used to render the button for any non-empty `text`, so on
 * a case-2 article the reader got a two-line summary, a "Read more"
 * button, and — on clicking it — the identical two lines, because the
 * collapsed box was taller than the text. The chevron rotated, so it
 * looked like it had worked. `overflowsCollapsedClamp()` below is the
 * check that was written for exactly this and never called; it is the
 * only thing standing between the reader and a button that lies.
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
  /** The outlet's full body where licensed, else the excerpt minus the
   *  sentences the summary points already carry. Never null here. */
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
    Only draw the control when there is genuinely something under it.

    This was `text.trim().length > 0` — a button on every article, for
    any text at all. The collapsed box is three lines tall, so on the
    many articles whose `text` is one or two lines the button sat below
    a box that was already showing everything: clicking it flipped the
    chevron, changed the label to "Show less", and revealed exactly the
    same words. That is the bug this component's own comment block used
    to explain away instead of fixing, and the estimate written for it
    sat unused two functions above.

    So: no text, or text that already fits in the collapsed box, gets a
    plain paragraph and no control. A licensed full body is thousands of
    characters and collapses correctly; a one-line excerpt simply is not
    a disclosure, and the "Read the full story on <outlet>" link below is
    the honest route to the rest.
  */
  const collapsible =
    text.trim().length > 0 && overflowsCollapsedClamp(text, language);

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
    // Nothing is hidden, so there is nothing to disclose. Render the
    // blocks as they came rather than welding them into one <p>: a
    // licensed body can legitimately be short enough to fit three lines
    // across two paragraphs, and joining those loses the paragraph the
    // outlet wrote. A one-paragraph excerpt yields one <p> here and
    // looks exactly as it always did.
    return (
      <div className={`mt-6 space-y-4 ${bodyClass}`}>
        {paragraphs.map((paragraph, index) => (
          <p key={index} lang={language ?? undefined} className="whitespace-pre-line">
            {paragraph}
          </p>
        ))}
      </div>
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
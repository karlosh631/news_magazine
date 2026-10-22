"use client";

import Link from "next/link";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import type { NewsCardArticle } from "@/components/NewsCard";
import { OptimizedImage } from "@/components/OptimizedImage";
import { SummaryPoints } from "@/components/SummaryPoints";
import { formatNewsDate } from "@/lib/date";
import { excerptWithoutOverlap } from "@/lib/excerpt";
import {
  focusableWithin,
  focusReturnTarget,
  focusThenHide,
  isEscapeKey,
  lockBodyScroll,
  nextFocusIndex,
  showThenRestore,
} from "@/lib/story-dialog";

/**
 * A story, opened on the reels page itself.
 *
 * THE REASON THIS EXISTS
 * ----------------------
 * A reader on /category/reels is watching a video feed. The two
 * columns of news drifting down either side of it are the site's
 * front page, folded into the edges of the screen, and a headline in
 * one of them is the only way from that page to a story without
 * leaving it.
 *
 * Until now that headline was an ordinary link, and following it
 * navigated away: the reels unmounted, the reader lost their place in
 * the feed, and coming back meant scrolling from the top again. On a
 * page whose whole content is a video the reader was in the middle of,
 * that is a real cost.
 *
 * So the story opens HERE, over the feed, in a dialog. The reels stay
 * mounted behind it. The columns stop drifting while it is open —
 * a column that keeps moving under a paragraph of text moves the words
 * out from under the reader's eyes — and they start again from exactly
 * where they stopped when it is closed.
 *
 * WHAT MAKES IT A DIALOG
 * ----------------------
 * A dialog is not a styled <div>. It is a contract with the
 * assistive technology and the keyboard:
 *
 *   - it is named, and it is modal (`role="dialog"`,
 *     `aria-modal="true"`, `aria-labelledby` pointing at the
 *     headline);
 *   - the focus moves into it when it opens and cannot leave it while
 *     it is open;
 *   - Escape closes it, and so does a click on the backdrop;
 *   - the page behind it is out of the accessibility tree, so a screen
 *     reader cannot wander into the reels while the story is up;
 *   - the page behind it does not scroll;
 *   - when it closes, the focus goes back to the card that opened it,
 *     so the reader is exactly where they were.
 *
 * The rules themselves live in lib/story-dialog.ts, which is where
 * they can be tested without a browser.
 */
export function StoryDialog({
  article,
  side,
  onClose,
  returnFocusTo,
  background,
}: {
  article: NewsCardArticle;
  /**
   * Which column the story was opened from.
   *
   * The story opens IN that column, not over the middle of the page.
   * A reader who clicks a headline on the left is reading a story
   * about the left-hand news, and a panel that drops into the middle
   * of the screen - over the video they were watching, between the
   * two columns - says "this is a modal" rather than "this is the
   * story you clicked".
   *
   * Null means there is no column, and the story is centred.
   */
  side?: "left" | "right" | null;
  onClose: () => void;
  /**
   * The card that opened the story. The focus goes back to it when the
   * story closes, so the reader is left where they were rather than at
   * the top of the document.
   */
  returnFocusTo: HTMLElement | null;
  /**
   * The reels page behind the story. It is taken out of the
   * accessibility tree while the story is open.
   */
  background: HTMLElement | null;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  const [imageFailed, setImageFailed] = useState(false);

  const language = article.language === "ne" ? "ne" : "en";
  const points = article.summary_points ?? [];
  // The excerpt minus whatever the four points already say, exactly as
  // the article page does it — otherwise the story says the same
  // thing twice on one screen.
  const body = excerptWithoutOverlap(article.excerpt, points);
  const image = article.image_highres_url ?? article.featured_image_url ?? null;

  /*
    The card that opened the story, in a ref.

    The feed re-renders every fifteen seconds when the header's poll
    says the world moved on, and a re-render hands the dialog a new
    `returnFocusTo` value. The focus must go back to the card the
    reader actually clicked, so the latest value is kept here and read
    once, when the story closes.
  */
  const restoreRef = useRef<HTMLElement | null>(returnFocusTo);
  useEffect(() => {
    restoreRef.current = returnFocusTo;
  }, [returnFocusTo]);

  // Escape closes the story, wherever the focus happens to be.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isEscapeKey(event.key)) return;
      event.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  /*
    The focus cannot leave the story, even by a route the Tab key does
    not take.

    Screen readers and browser find-in-page can both move the focus
    somewhere outside the dialog, and a modal dialog that lets the
    reader wander off into the page behind it is not a modal dialog.
    Anything that lands outside the panel is pulled back in.
  */
  useEffect(() => {
    const onFocusIn = (event: FocusEvent) => {
      const panel = panelRef.current;
      if (!panel) return;
      const target = event.target as Node | null;
      if (target && panel.contains(target)) return;
      const items = focusableWithin(panel);
      (items[0] ?? panel).focus({ preventScroll: true });
    };
    document.addEventListener("focusin", onFocusIn);
    return () => document.removeEventListener("focusin", onFocusIn);
  }, []);

  /*
    Open the story, and close it again.

    Everything this does is undone on the way out, in the reverse
    order, because the order is what makes it work:

      in  — focus first, THEN hide the background, then lock the page;
      out — unlock the page, show the background, THEN give the focus
            back.

    Chrome refuses to apply `aria-hidden` to a subtree that still holds
    the focus, and it refuses to focus a node that is inside one, so
    doing either of these in the wrong order leaves the page in a state
    where the background is neither hidden nor reachable.
  */
  useEffect(() => {
    focusThenHide(background, () => {
      panelRef.current?.focus({ preventScroll: true });
    });

    const unlock = lockBodyScroll(document.body);

    return () => {
      unlock();
      showThenRestore(background, () => {
        /*
          The focus goes back to the card that opened the story, so the
          reader is left exactly where they were. If that card is one
          of the drifting column's hidden repeats — the columns repeat
          their list so that the loop is seamless, and the repeats are
          hidden from assistive technology — the same story's card in
          the visible copy is used instead, because a card a screen
          reader cannot see cannot hold the focus.
        */
        const trigger = restoreRef.current;
        const articleId = trigger?.getAttribute("data-article-id");
        const sameStory = articleId
          ? Array.from(
              document.querySelectorAll<HTMLElement>("[data-article-id]")
            ).filter(
              (element) =>
                element.getAttribute("data-article-id") === articleId
            )
          : [];

        const target =
          (trigger && trigger.isConnected
            ? focusReturnTarget(trigger, sameStory)
            : null) ??
          /*
            The card is gone — the feed was refreshed underneath the
            story and the card it replaced was dropped. The focus still
            has to go somewhere inside the page, or it falls onto the
            body and the reader's next Tab starts from the top of the
            document.
          */
          focusableWithin(background)[0] ??
          null;
        target?.focus({ preventScroll: true });
      });
    };
  }, [background]);

  /*
    The focus cannot leave the story.

    Tab is handled here rather than left to the browser, because the
    browser's own Tab order runs off the end of the dialog and into
    the page behind it. Every Tab is intercepted and the focus is
    moved by hand, wrapping at both ends.
  */
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Tab") return;

    const items = focusableWithin(panelRef.current);
    if (items.length === 0) {
      event.preventDefault();
      panelRef.current?.focus({ preventScroll: true });
      return;
    }

    const current = items.indexOf(document.activeElement as HTMLElement);
    const next = nextFocusIndex(current, items.length, event.shiftKey ? -1 : 1);
    event.preventDefault();
    items[next]?.focus({ preventScroll: true });
  };

  return (
    /*
      The backdrop. It is fixed to the viewport, so it is not clipped by
      the reels page's own `overflow-hidden` and it never adds a
      page-level scrollbar of its own — the page behind it is locked
      instead.
    */
    <div
      className={
        side
          ? // IN A COLUMN.
            //
            // The story belongs to the column it was clicked in, so it
            // opens there - and the rest of the page is left alone.
            // There is no dimming over the whole screen: the reel
            // keeps playing and the other column keeps drifting, so
            // the page carries on around the story being read.
            //
            // On a phone there are no side columns at all, so the
            // story is centred there exactly as it always was. The
            // column layout only starts at the `lg` breakpoint, where
            // the three-column grid does.
            "fixed inset-0 z-[70] flex items-center justify-center overflow-y-auto p-4 sm:p-6 " +
            (side === "left"
              ? "lg:justify-start lg:pl-6"
              : "lg:justify-end lg:pr-6")
          : // No column: the story is the whole page's business, and
            // the backdrop dims it.
            "fixed inset-0 z-[70] flex items-center justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm sm:p-6"
      }
      onClick={(event) => {
        // A click on the backdrop closes the story. A click that
        // started inside the panel did not, so the target is checked:
        // the panel is a child of the backdrop and its clicks bubble
        // up here.
        if (event.target !== event.currentTarget) return;
        onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        // The panel itself takes the focus when the story opens, so a
        // screen reader announces the story's name and the fact that
        // it is a dialog rather than announcing a button.
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className={
          side
            ? // The column's own width.
              //
              // The reels page is a three-column grid: a 420px middle
              // column, a 24px gap and a 24px page margin on each side
              // of it. So a side column ends 234px short of the
              // middle of the screen, and the panel is bounded by
              // that - the story fills the column it was clicked in
              // and does not spill over the video.
              "relative flex max-h-[calc(100dvh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white text-gray-900 shadow-2xl focus:outline-none lg:max-w-[calc(50%-14.625rem)] dark:bg-gray-900 dark:text-gray-100"
            : "relative flex max-h-[calc(100dvh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white text-gray-900 shadow-2xl focus:outline-none dark:bg-gray-900 dark:text-gray-100"
        }
      >
        {/* ================= HEAD ================= */}
        <div className="flex items-start justify-between gap-4 border-b border-gray-200 p-5 dark:border-gray-700">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              {article.source_name_snapshot ?? "News"}
              {article.published_at
                ? ` · ${formatNewsDate(article.published_at, language)}`
                : ""}
            </p>
            <h2
              id={titleId}
              className="mt-1 font-headline text-xl font-bold leading-snug text-gray-900 sm:text-2xl dark:text-gray-100"
            >
              {article.headline}
            </h2>
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Close story"
            className="shrink-0 rounded-lg p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 dark:hover:bg-gray-800 dark:hover:text-gray-200"
          >
            <span aria-hidden="true" className="block text-xl leading-none">
              ×
            </span>
          </button>
        </div>

        {/* ================= BODY ================= */}
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {image && !imageFailed && (
            <div className="relative mb-4 aspect-video w-full overflow-hidden rounded-xl bg-gray-100 dark:bg-gray-800">
              <OptimizedImage
                src={image}
                alt={article.headline}
                sizes="(min-width: 768px) 42rem, 100vw"
                onError={() => setImageFailed(true)}
                className="h-full w-full object-cover"
              />
            </div>
          )}

          {points.length > 0 && <SummaryPoints points={points} />}

          {body && (
            <p className="mt-4 text-base leading-7 text-gray-700 dark:text-gray-300">
              {body}
            </p>
          )}

          {!body && points.length === 0 && (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              No summary is available for this story. Read the full report at
              the original source.
            </p>
          )}

          <div className="mt-6 flex flex-wrap items-center gap-3">
            {/*
              The full page is still one click away for the reader who
              wants the whole story, the related coverage and the
              share buttons — but it is a choice now, not the only
              way out of a headline.
            */}
            <Link
              href={`/article/${article.slug}`}
              className="inline-flex min-h-[44px] items-center rounded-lg border-2 border-blue-600 bg-blue-50 px-4 text-sm font-semibold text-blue-800 transition hover:bg-blue-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 dark:border-blue-500 dark:bg-blue-950/50 dark:text-blue-200 dark:hover:bg-blue-900/50"
            >
              Open the full page
            </Link>

            {article.source_article_url && (
              <a
                href={article.source_article_url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="inline-flex min-h-[44px] items-center rounded-lg border border-gray-300 px-4 text-sm font-semibold text-gray-700 transition hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
              >
                Read the full story on{" "}
                {article.source_name_snapshot ?? "the original source"} ↗
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

import { expect, test } from "@playwright/test";
import { createElement } from "react";
import { renderToString } from "react-dom/server";

import {
  driftOffsetAfter,
  isColumnHeld,
  isDriftHeld,
  shouldDriftRun,
} from "@/lib/drift";
import {
  focusReturnTarget,
  focusThenHide,
  hideBackground,
  isEscapeKey,
  isPlainActivation,
  isHiddenFromAssistiveTechnology,
  nextFocusIndex,
  showBackground,
  showThenRestore,
} from "@/lib/story-dialog";
import { loadComponent } from "./support/tsx-module";

/**
 * Opening a story from the reels page, without leaving it.
 *
 * THE BUG THIS EXISTS FOR
 * -----------------------
 * /category/reels is a full-viewport video feed with the site's front
 * page folded into the two columns that drift down either side of it.
 * A headline in one of those columns was an ordinary link, and
 * following it threw the reader out of the video they were in the
 * middle of: the reels unmounted, the place in the feed was lost, and
 * coming back meant scrolling from the top again.
 *
 * So a headline now opens the story ON THE PAGE, over the feed, and
 * three things have to be true at once for that to be any good:
 *
 *   1. the story opens where the reader already is — the reels stay
 *      mounted behind it;
 *   2. the columns HOLD while it is open — a column that keeps moving
 *      under a paragraph of text moves the words out from under the
 *      reader's eyes — and they resume from exactly where they
 *      stopped, not from the top;
 *   3. it is a real dialog: named, modal, the focus goes in and cannot
 *      get out, Escape closes it, the page behind it is out of the
 *      accessibility tree, and the focus comes back to the card that
 *      opened it.
 *
 * These specs pin the rules. They are plain functions over plain data
 * so that they run in milliseconds without a browser, which is what
 * this repository's suite does — the one thing here that needs a real
 * DOM (which elements the dialog can put the focus on) is the one
 * thing that cannot be pinned this way, and everything it depends on
 * is.
 */

/** One story, as the side columns receive it. */
const story = {
  id: "story-1",
  slug: "bagmati-bridge-opens",
  headline: "The Bagmati bridge opens to traffic two years late",
  excerpt:
    "The first vehicles crossed the bridge on Monday morning, two years after the first phase was announced and eighteen months after the original opening date.",
  summary_points: [
    "The first vehicles crossed the bridge at nine in the morning.",
    "The second phase is due to finish next year.",
    "The final cost came in under the original estimate.",
  ],
  language: "en" as const,
  featured_image_url: null,
  image_highres_url: null,
  video_url: null,
  video_embed_url: null,
  audio_url: null,
  gif_url: null,
  published_at: "2026-10-20T04:30:00.000Z",
  source_name_snapshot: "The Kathmandu Post",
  source_article_url: "https://kathmandupost.com/story",
  source_count: 2,
};

/**
 * The real dialog, compiled and loaded.
 *
 * See tests/support/tsx-module.ts for why the component cannot simply
 * be imported: Playwright rewrites JSX into its own objects, and React
 * will not render those.
 */
const { StoryDialog } = loadComponent<
  typeof import("@/components/StoryDialog")
>("@/components/StoryDialog");

/** The rendered dialog, as the browser would receive it. */
const renderedDialog = () =>
  renderToString(
    createElement(StoryDialog, {
      article: story,
      onClose: () => {},
      returnFocusTo: null,
      background: null,
    })
  );

/** A stand-in for the reels page, recording what is done to it. */
function fakeBackground(log: string[]) {
  return {
    setAttribute: (name: string, value: string) =>
      log.push(`set ${name}=${value}`),
    removeAttribute: (name: string) => log.push(`remove ${name}`),
  } as unknown as Element;
}

test.describe("the columns hold still while a story is open", () => {
  test("an open story holds the columns", () => {
    // The reader opened a story to read it. The columns are directly
    // behind that story.
    expect(
      isDriftHeld({
        manuallyPaused: false,
        storyOpen: true,
        reduceMotion: false,
      })
    ).toBe(true);
  });

  test("the reader's own pause still holds the columns", () => {
    expect(
      isDriftHeld({
        manuallyPaused: true,
        storyOpen: false,
        reduceMotion: false,
      })
    ).toBe(true);
  });

  test("reduced motion is not drift at all", () => {
    expect(
      isDriftHeld({
        manuallyPaused: false,
        storyOpen: false,
        reduceMotion: true,
      })
    ).toBe(true);
  });

  test("with nothing holding them, the columns drift", () => {
    expect(
      isDriftHeld({
        manuallyPaused: false,
        storyOpen: false,
        reduceMotion: false,
      })
    ).toBe(false);
  });

  test("a held column does not move on a frame", () => {
    // This is the whole of "the drift holds": the column is not moved
    // at all, so it cannot drift anywhere — not forward, and not back
    // to the top.
    expect(
      shouldDriftRun({ held: true, hovered: false, hidden: false })
    ).toBe(false);
  });

  test("a column that is not held moves", () => {
    expect(
      shouldDriftRun({ held: false, hovered: false, hidden: false })
    ).toBe(true);
  });

  test("a column under the pointer, or in a hidden tab, does not move", () => {
    expect(
      shouldDriftRun({ held: false, hovered: true, hidden: false })
    ).toBe(false);
    expect(
      shouldDriftRun({ held: false, hovered: false, hidden: true })
    ).toBe(false);
  });

  test("the column resumes from exactly where it paused", () => {
    // The offset is the only record of where the column has got to,
    // and it is only written when the column is allowed to move. So
    // a hold leaves it alone, and the column carries on from there.
    let offset = 0;
    offset = driftOffsetAfter(offset, 16, 1000);
    const pausedAt = offset;

    // While the story is open, nothing is written: the column is
    // held, so the offset is not touched.
    for (let frame = 0; frame < 60; frame += 1) {
      if (
        !shouldDriftRun({ held: true, hovered: false, hidden: false })
      ) {
        continue;
      }
      offset = driftOffsetAfter(offset, 16, 1000);
    }
    expect(offset).toBe(pausedAt);

    // The story closes, and the column carries on from the same place
    // rather than starting again.
    offset = driftOffsetAfter(offset, 16, 1000);
    expect(offset).toBeGreaterThan(pausedAt);
    expect(offset).toBeCloseTo(pausedAt + 0.16, 5);
  });

  test("a janked frame does not teleport the column", () => {
    // A tab that was throttled hands the loop a delta of seconds. At
    // 10px/s that is a visible jump, and for a track that has to wrap
    // cleanly, a jump straight past the seam.
    expect(driftOffsetAfter(0, 5000, 1000)).toBe(
      driftOffsetAfter(0, 100, 1000)
    );
  });

  test("the column wraps at the end of a copy", () => {
    // One full copy of travel and we are back where we started
    // looking, because the copy below is identical to the copy above.
    expect(driftOffsetAfter(999.5, 100, 1000)).toBeCloseTo(0.5, 5);
  });
});

test.describe("the story opens on the page the reader is on", () => {
  test("a plain click opens the story in place", () => {
    expect(
      isPlainActivation({
        button: 0,
        metaKey: false,
        ctrlKey: false,
        shiftKey: false,
        altKey: false,
      })
    ).toBe(true);
  });

  test("a modified click is left to the browser", () => {
    // ctrl-click and cmd-click open a new tab, shift-click opens a new
    // window, and the middle mouse pastes the link. Those are things
    // the reader asked for explicitly.
    const modified = {
      button: 0,
      metaKey: false,
      ctrlKey: false,
      shiftKey: false,
      altKey: false,
    };
    expect(isPlainActivation({ ...modified, ctrlKey: true })).toBe(false);
    expect(isPlainActivation({ ...modified, metaKey: true })).toBe(false);
    expect(isPlainActivation({ ...modified, shiftKey: true })).toBe(false);
    expect(isPlainActivation({ ...modified, altKey: true })).toBe(false);
  });

  test("a click that is not the left button is not an activation", () => {
    expect(
      isPlainActivation({
        button: 1,
        metaKey: false,
        ctrlKey: false,
        shiftKey: false,
        altKey: false,
      })
    ).toBe(false);
  });

  test("Escape closes the story", () => {
    expect(isEscapeKey("Escape")).toBe(true);
    // The old name for the same key, still reported by some browsers.
    expect(isEscapeKey("Esc")).toBe(true);
    expect(isEscapeKey("Enter")).toBe(false);
    expect(isEscapeKey("Tab")).toBe(false);
  });
});

test.describe("the focus cannot leave the story", () => {
  test("the focus wraps at the end", () => {
    // Tab off the last control lands on the first one.
    expect(nextFocusIndex(2, 3, 1)).toBe(0);
  });

  test("the focus wraps at the beginning", () => {
    // Shift+Tab off the first control lands on the last one.
    expect(nextFocusIndex(0, 3, -1)).toBe(2);
  });

  test("the focus moves on one control at a time", () => {
    expect(nextFocusIndex(0, 3, 1)).toBe(1);
    expect(nextFocusIndex(1, 3, -1)).toBe(0);
  });

  test("the focus starts at the first control", () => {
    // The focus is on the dialog itself rather than on one of its
    // controls, which is where it lands when the story opens.
    expect(nextFocusIndex(-1, 3, 1)).toBe(0);
    expect(nextFocusIndex(-1, 3, -1)).toBe(2);
  });

  test("a dialog with nothing in it has nowhere to send the focus", () => {
    expect(nextFocusIndex(0, 0, 1)).toBe(-1);
    expect(nextFocusIndex(-1, 0, -1)).toBe(-1);
  });
});

test.describe("the page behind the story", () => {
  test("the focus leaves the card before the background is hidden", () => {
    // Chrome refuses to apply aria-hidden to a subtree that still
    // holds the focus — it blocks the attribute and says so in the
    // console — so the order here is the difference between a
    // background that is hidden and one that only looks hidden.
    const log: string[] = [];
    focusThenHide(fakeBackground(log), () => log.push("focus"));
    expect(log).toEqual(["focus", "set aria-hidden=true"]);
  });

  test("the background comes back before the focus does", () => {
    // The card that gets the focus back lives inside the background,
    // so the background has to be visible again first.
    const log: string[] = [];
    showThenRestore(fakeBackground(log), () => log.push("focus"));
    expect(log).toEqual(["remove aria-hidden", "focus"]);
  });

  test("the background is hidden from assistive technology", () => {
    const log: string[] = [];
    const background = fakeBackground(log);

    hideBackground(background);
    showBackground(background);

    expect(log).toEqual(["set aria-hidden=true", "remove aria-hidden"]);
  });
});

test.describe("the focus goes back where it came from", () => {
  /** A card, visible or not. */
  const card = (hidden: boolean) =>
    ({
      closest: (selector: string) =>
        hidden && selector === '[aria-hidden="true"]' ? {} : null,
    }) as unknown as HTMLElement;

  test("the card that was clicked gets the focus back", () => {
    const clicked = card(false);
    expect(focusReturnTarget(clicked, [])).toBe(clicked);
  });

  test("a card inside a hidden copy cannot hold the focus", () => {
    // The drifting columns repeat their list so that the loop is
    // seamless, and the repeats are hidden from assistive technology.
    // A card in one of those repeats is not there, as far as a screen
    // reader is concerned, so it cannot take the focus back.
    const clicked = card(true);
    expect(focusReturnTarget(clicked, [])).toBeNull();
  });

  test("the same story's visible card takes it instead", () => {
    const clicked = card(true);
    const visible = card(false);
    expect(focusReturnTarget(clicked, [clicked, visible])).toBe(visible);
  });

  test("a hidden subtree is recognised", () => {
    expect(isHiddenFromAssistiveTechnology(card(true))).toBe(true);
    expect(isHiddenFromAssistiveTechnology(card(false))).toBe(false);
    expect(isHiddenFromAssistiveTechnology(null)).toBe(false);
  });
});

test.describe("the story is a real dialog", () => {
  test("the panel is announced as a modal dialog", () => {
    const html = renderedDialog();
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
  });

  test("the dialog is named after the story", () => {
    // A dialog with no name is announced as "dialog" and nothing
    // else, which tells a screen reader reader nothing at all.
    const html = renderedDialog();
    const labelledBy = html.match(/aria-labelledby="([^"]+)"/)?.[1];
    const headingId = html.match(/<h2[^>]*id="([^"]+)"/)?.[1];

    expect(labelledBy).toBeTruthy();
    expect(headingId).toBe(labelledBy);
    expect(html).toContain(story.headline);
  });

  test("there is a close button with an accessible name", () => {
    const html = renderedDialog();
    expect(html).toContain('aria-label="Close story"');
  });

  test("the story is on the page, not just a link to it", () => {
    // The reader asked for the story. A dialog that only holds a link
    // to the story would be a slower way of getting to it.
    const html = renderedDialog();
    expect(html).toContain(story.summary_points[0]!);
    expect(html).toContain("The first vehicles crossed the bridge");
  });

  test("the full page is still one click away", () => {
    const html = renderedDialog();
    expect(html).toContain(`/article/${story.slug}`);
  });

  test("the outlet's own story is still offered", () => {
    const html = renderedDialog();
    expect(html).toContain(story.source_article_url!);
  });
});

/**
 * THE STORY OPENS IN ITS OWN COLUMN
 * ---------------------------------
 * The reader asked for the story to open where it was clicked, not in
 * a panel over the middle of the screen.
 *
 * The middle of the screen is the video. A story that drops into the
 * middle covers the thing the reader was watching, and it covers it
 * between the two columns it belongs to - so it says "this is a modal
 * that has taken over" rather than "this is the story you clicked".
 *
 * And only the column the story came from stops moving. The other one
 * keeps drifting and the reel keeps playing, so the page stays alive
 * around the story being read.
 */
test.describe("the story opens in its own column", () => {
  const OPEN = { manuallyPaused: false, storyOpen: true, reduceMotion: false };
  const CLOSED = { manuallyPaused: false, storyOpen: false, reduceMotion: false };

  test("a story in the left column holds only the left column", () => {
    expect(isColumnHeld(OPEN, "left", "left")).toBe(true);
    expect(isColumnHeld(OPEN, "right", "left")).toBe(false);
  });

  test("a story in the right column holds only the right column", () => {
    expect(isColumnHeld(OPEN, "right", "right")).toBe(true);
    expect(isColumnHeld(OPEN, "left", "right")).toBe(false);
  });

  test("with no story, neither column is held", () => {
    expect(isColumnHeld(CLOSED, "left", null)).toBe(false);
    expect(isColumnHeld(CLOSED, "right", null)).toBe(false);
  });

  test("the reader's own pause holds both columns", () => {
    const paused = { manuallyPaused: true, storyOpen: false, reduceMotion: false };
    expect(isColumnHeld(paused, "left", null)).toBe(true);
    expect(isColumnHeld(paused, "right", null)).toBe(true);
  });

  test("reduced motion holds both columns", () => {
    const still = { manuallyPaused: false, storyOpen: false, reduceMotion: true };
    expect(isColumnHeld(still, "left", null)).toBe(true);
    expect(isColumnHeld(still, "right", null)).toBe(true);
  });

  test("a story with no column holds both", () => {
    expect(isColumnHeld(OPEN, "left", null)).toBe(true);
    expect(isColumnHeld(OPEN, "right", null)).toBe(true);
  });
});

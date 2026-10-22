/**
 * The drifting news columns on the reels page.
 *
 * WHY THESE RULES LIVE HERE
 * -------------------------
 * The columns are the only thing on the site that moves on its own, and
 * "it moves" is a rule rather than a rendering detail: it moves at a
 * fixed speed, it holds still the moment anything asks it to, and it
 * holds still WHERE IT IS rather than snapping back to the top.
 *
 * The rules are therefore written as plain functions with no React and
 * no DOM in them, which is what lets the suite in tests/ pin them
 * without launching a browser.
 */

/** How fast the side columns drift, in CSS pixels per second. */
export const DRIFT_PX_PER_SEC = 10;

/**
 * The longest frame the drift is willing to believe.
 *
 * A tab that was throttled, or a frame that was janked, hands the
 * animation loop a delta of seconds. At 10px/s that is a visible jump,
 * and for a track that has to wrap cleanly, a jump straight past the
 * seam. So the delta is capped and the column moves at most this much
 * in one frame, however long the frame actually was.
 */
export const MAX_FRAME_MS = 100;

/**
 * Why the columns might be held still.
 *
 * Three reasons, and all three are the reader's:
 *
 *   - they pressed the pause button;
 *   - they opened a story, and the columns are behind the story;
 *   - they asked the operating system for no motion at all.
 */
export type DriftHold = {
  /** The reader pressed the pause button under the feed. */
  manuallyPaused: boolean;
  /** A story is open, in front of the columns. */
  storyOpen: boolean;
  /** The reader asked for reduced motion. */
  reduceMotion: boolean;
};

/**
 * Whether the columns are being held still.
 *
 * The story case is the one this was written for: the reader opened a
 * story to read it, and the columns are directly behind that story. A
 * column that keeps drifting under a paragraph of text is a column
 * that moves the words out from under the reader's eyes.
 */
export function isDriftHeld(hold: DriftHold): boolean {
  return hold.manuallyPaused || hold.storyOpen || hold.reduceMotion;
}

/**
 * Whether ONE column is being held still.
 *
 * A story opened from the left column opens IN the left column, and
 * only the left column stops: the other one keeps drifting and the
 * reel keeps playing, so the page carries on around the story the
 * reader is reading. Holding both columns — which is what a plain
 * "is anything open?" check does — stops the whole page for a story
 * that is only covering half of it.
 *
 * A story with no column (there is none today, but the type allows
 * it) holds both, because there is no column to say which one it
 * belongs to.
 */
export function isColumnHeld(
  hold: DriftHold,
  side: "left" | "right",
  storySide: "left" | "right" | null
): boolean {
  if (hold.manuallyPaused || hold.reduceMotion) return true;
  if (!hold.storyOpen) return false;
  if (storySide === null) return true;
  return storySide === side;
}

/**
 * Whether the columns may move on this frame.
 *
 * The hover case is here because grabbing a headline to read it is not
 * the moment to have the floor move under it, and the hidden case is
 * because nothing should move behind a tab nobody is looking at.
 */
export function shouldDriftRun(state: {
  /** The columns are being held still for one of the reasons above. */
  held: boolean;
  /** The pointer or the keyboard focus is inside a column. */
  hovered: boolean;
  /** The tab is in the background. */
  hidden: boolean;
}): boolean {
  return !state.held && !state.hovered && !state.hidden;
}

/**
 * Where the column is after one frame.
 *
 * The offset is the ONLY record of where the column has got to, and it
 * is never rewritten while the column is held — that is what "frozen
 * exactly where it is" means, and it is why a pause cannot end up
 * resetting the column to the top.
 *
 * The wrap is one copy of the list: the copy below the fold is
 * identical to the copy above it, so a full copy of travel puts the
 * reader back looking at the same card.
 */
export function driftOffsetAfter(
  current: number,
  deltaMs: number,
  copyHeight: number,
  pxPerSecond: number = DRIFT_PX_PER_SEC
): number {
  const delta = Math.min(MAX_FRAME_MS, Math.max(0, deltaMs));
  const next = current + (pxPerSecond * delta) / 1000;
  if (copyHeight > 0 && next >= copyHeight) return next - copyHeight;
  return next;
}

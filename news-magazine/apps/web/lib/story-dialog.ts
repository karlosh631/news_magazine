/**
 * The story dialog on the reels page.
 *
 * A reader who clicks a headline in one of the drifting columns gets
 * that story WITHOUT LEAVING THE REELS. The reels stay mounted behind
 * the story, the columns hold still while it is open, and they start
 * moving again from exactly where they stopped the moment it is
 * closed.
 *
 * WHY THE RULES LIVE HERE
 * -----------------------
 * The accessibility contract of a dialog is a set of rules, not a
 * picture: it is named, it is modal, the focus goes into it, the focus
 * cannot leave it, the focus comes back to the card that opened it, the
 * background is out of the accessibility tree, and the page behind it
 * does not scroll.
 *
 * They are written here as plain functions over plain data so the
 * suite in tests/ can pin them without launching a browser, which is
 * what this repository's test setup does.
 */

/**
 * The keys that close the dialog.
 *
 * "Esc" is the old name for the same key and is still what some older
 * browsers report, so it is accepted as well.
 */
export function isEscapeKey(key: string): boolean {
  return key === "Escape" || key === "Esc";
}

/**
 * Whether a click on a card should open the story in place.
 *
 * A modified click is the browser's business and not ours:
 * ctrl-click and cmd-click open a new tab, shift-click opens a new
 * window, and a middle-click pastes the link. All of those are things
 * a reader asks for explicitly, and quietly turning them into an
 * in-page dialog would be overriding a deliberate choice.
 *
 * Only a plain left click opens the story where the reader is.
 */
export function isPlainActivation(event: {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}): boolean {
  if (event.button !== 0) return false;
  return !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

/**
 * Where the focus goes when the reader presses Tab inside the dialog.
 *
 * The arithmetic of a focus trap, and nothing else: which element has
 * the focus now, how many the dialog holds, and which way the reader
 * is moving. The answer wraps, so Tab off the last element lands on
 * the first and Shift+Tab off the first lands on the last — which is
 * what makes the focus unable to leave.
 *
 * `current` is -1 when the focus is on the dialog itself rather than
 * on one of its controls, and the answer is then the first control for
 * a forward Tab and the last for a backward one.
 */
export function nextFocusIndex(
  current: number,
  count: number,
  direction: 1 | -1
): number {
  if (count <= 0) return -1;
  if (current < 0) return direction === 1 ? 0 : count - 1;
  return (current + direction + count) % count;
}

/**
 * Everything that can hold the focus, in the order the reader reaches it.
 */
export const FOCUSABLE_SELECTOR = [
  "a[href]",
  "area[href]",
  "button:not([disabled])",
  'input:not([disabled]):not([type="hidden"])',
  "select:not([disabled])",
  "textarea:not([disabled])",
  "iframe",
  "object",
  "embed",
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

/**
 * The focusable elements inside a container, in tab order.
 *
 * Two things are filtered out:
 *
 *   - anything that is not actually on screen. A hidden control is not
 *     a tab stop, and including it would make the trap land on
 *     something the reader cannot see.
 *   - anything inside an `aria-hidden` subtree. The drifting columns
 *     repeat their list, and the repeats are hidden from assistive
 *     technology; putting the focus in one of those would be focusing
 *     something the reader cannot perceive.
 *
 * This one needs a real DOM, so it is the one function here that the
 * browser-free suite does not call.
 */
export function focusableWithin(root: ParentNode | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
  ).filter(
    (element) =>
      element.getClientRects().length > 0 &&
      element.closest('[aria-hidden="true"]') === null
  );
}

/**
 * Whether an element is inside a subtree that assistive technology
 * cannot see.
 */
export function isHiddenFromAssistiveTechnology(
  element: Element | null
): boolean {
  return Boolean(element?.closest('[aria-hidden="true"]'));
}

/**
 * The card that should get the focus back when the story closes.
 *
 * The card the reader clicked is the obvious answer, and it is the
 * right one — unless the column had already repeated that story into
 * one of its hidden copies of itself, which the drifting columns do so
 * that the loop is seamless. A card inside a hidden copy cannot hold
 * the focus: as far as a screen reader is concerned it is not there.
 *
 * So the same story's card in the visible copy is used instead, and
 * the card that was clicked is only used when it is the visible one.
 */
export function focusReturnTarget(
  trigger: HTMLElement | null,
  sameStory: HTMLElement[]
): HTMLElement | null {
  if (trigger && !isHiddenFromAssistiveTechnology(trigger)) return trigger;
  for (const candidate of sameStory) {
    if (isHiddenFromAssistiveTechnology(candidate)) continue;
    return candidate;
  }
  return null;
}

/**
 * Take the reels page out of the accessibility tree.
 *
 * While the story is open the page behind it is not something a
 * screen reader should be able to walk into: the story is modal, and
 * what is behind a modal dialog is not available until the dialog
 * closes.
 */
export function hideBackground(background: Element | null): void {
  background?.setAttribute("aria-hidden", "true");
}

/**
 * Put the reels page back into the accessibility tree.
 */
export function showBackground(background: Element | null): void {
  background?.removeAttribute("aria-hidden");
}

/**
 * Open the story: move the focus FIRST, then hide the background.
 *
 * The order is the whole point and it is not cosmetic. Chrome refuses
 * to apply `aria-hidden` to a subtree that still holds the focus — it
 * blocks the attribute and says so in the console — so a background
 * that was hidden before the focus moved would not actually be hidden.
 * The focus has to leave the card that opened the story first.
 */
export function focusThenHide(
  background: Element | null,
  focus: () => void
): void {
  focus();
  hideBackground(background);
}

/**
 * Close the story: show the background, then give the focus back.
 *
 * The mirror image of `focusThenHide`, and for the same reason: the
 * card that gets the focus back lives inside the background, so the
 * background has to be visible again before the focus returns to it.
 */
export function showThenRestore(
  background: Element | null,
  restore: () => void
): void {
  showBackground(background);
  restore();
}

/**
 * Lock the page behind the dialog.
 *
 * The reels page does not scroll — the feed scrolls inside itself —
 * so this is belt and braces. But a dialog that lets the page behind
 * it scroll is a dialog the reader can slide out from under, and the
 * lock is what makes Escape and the close button the only ways out.
 *
 * The scrollbar's width is given back as padding so that locking the
 * page does not shift the layout sideways by the width of a scrollbar
 * that is no longer there.
 */
export function lockBodyScroll(body: HTMLElement | null): () => void {
  if (!body) return () => {};
  const previousOverflow = body.style.overflow;
  const previousPaddingRight = body.style.paddingRight;
  const scrollbarWidth =
    window.innerWidth - document.documentElement.clientWidth;

  body.style.overflow = "hidden";
  if (scrollbarWidth > 0) {
    body.style.paddingRight = `${scrollbarWidth}px`;
  }

  return () => {
    body.style.overflow = previousOverflow;
    body.style.paddingRight = previousPaddingRight;
  };
}

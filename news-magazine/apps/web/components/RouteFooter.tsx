"use client";

import { usePathname } from "next/navigation";

import { Footer } from "@/components/Footer";

/**
 * The footer, on every page except the ones that fill the viewport.
 *
 * WHY THIS IS A CLIENT COMPONENT
 * ------------------------------
 * The layout renders the footer once, for the whole site, and a layout
 * is a server component that never learns the pathname. A route that
 * wants different chrome therefore needs the client to say which route
 * it is on, and `usePathname` is that answer.
 *
 * WHY THE REELS PAGE IS THE EXCEPTION
 * -----------------------------------
 * /category/reels is a full-viewport experience: the middle column is
 * a phone-style snap scroller and the two columns beside it drift
 * downwards for ever. It sizes itself to the viewport minus the header
 * and clips its own overflow, so a footer underneath it cannot be
 * scrolled to - but it IS 150px of document, and 150px of document past
 * the viewport is exactly what puts a scrollbar on the right edge of
 * the screen, next to the drifting news column. A reader who sees
 * that scrollbar is looking at a page that believes it is bigger than
 * it is.
 *
 * The footer also has nothing to offer that route: it is six category
 * links the header already carries, and the page has no room for them.
 *
 * The check is a prefix match so that a future sub-route of the reels
 * section is covered by the same rule rather than by a second entry
 * here.
 */
export function RouteFooter() {
  const pathname = usePathname();

  if (pathname?.startsWith("/category/reels")) {
    return null;
  }

  return <Footer />;
}

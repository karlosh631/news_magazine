import { expect, test } from "@playwright/test";

import { searchHitHref } from "../components/SearchModal";

/**
 * Where a search result takes the reader.
 *
 * The reader searched for a story this site is running, and the result
 * opened the publisher's site in a new tab. The cause was one missing
 * field: the search endpoint left the story's own page (its slug) out
 * of its answer, and the dropdown asks for that field to decide
 * between an internal link and an outbound one.
 *
 * So every result was treated as an external link, and the reader was
 * sent away from the site that had already found the story.
 */

type Hit = {
  id: string | null;
  slug?: string | null;
  source_article_url?: string | null;
};

test.describe("a search result", () => {
  test("opens inside this site when we have the story's own page", () => {
    const { href, external } = searchHitHref({
      id: "1",
      slug: "national-balen-1234-2026-10-23",
      source_article_url: "https://publisher.example/news/balen",
    });

    expect(href).toBe("/article/national-balen-1234-2026-10-23");
    expect(external).toBe(false);
  });

  test("opens the outlet when the story was re-scraped and we have no page", () => {
    const { href, external } = searchHitHref({
      id: null,
      slug: null,
      source_article_url: "https://publisher.example/news/balen",
    });

    expect(href).toBe("https://publisher.example/news/balen");
    expect(external).toBe(true);
  });

  test("an empty slug is an empty slug, not a link to /article/", () => {
    // The bug was not "the slug was missing" - it was "the slug was
    // missing and nothing noticed". An empty string must not become
    // /article/, which would 404.
    const { href, external } = searchHitHref({
      id: "2",
      slug: "",
      source_article_url: "https://publisher.example/news/balen",
    });

    expect(href).toBe("https://publisher.example/news/balen");
    expect(external).toBe(true);
  });

  test("a hit with neither a slug nor a url does not link to nowhere", () => {
    const { href, external } = searchHitHref({ id: "3", slug: null });

    expect(href).toBe("#");
    expect(external).toBe(true);
  });
});

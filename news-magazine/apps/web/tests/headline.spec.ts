import { expect, test } from "@playwright/test";

import { cleanHeadline, collapseRepeatedHeadline } from "../lib/headline";

/**
 * A headline that says the same thing twice.
 *
 * Some publishers put the headline in their markup twice — once as the
 * visible heading and once in a second element the desktop and the
 * phone each use — and the listing parser reads the container rather
 * than the heading. The stored headline then reads:
 *
 *     Tanker with Russian diesel heads to New York under Trump deal
 *     Tanker with Russian diesel heads to New York under Trump deal
 *
 * and the reader sees it twice: in the search dropdown and on the card.
 *
 * The rule is strict on purpose. A headline that merely mentions
 * itself is news, not a bug.
 */

test.describe("a headline that repeats itself", () => {
  test("the second copy is dropped", () => {
    const doubled =
      "Tanker with Russian diesel heads to New York under Trump deal Tanker with Russian diesel heads to New York under Trump deal";
    expect(collapseRepeatedHeadline(doubled)).toBe(
      "Tanker with Russian diesel heads to New York under Trump deal"
    );
  });

  test("a headline that merely mentions itself is left alone", () => {
    const headline = "Man arrested after man arrested in a theft case";
    expect(collapseRepeatedHeadline(headline)).toBe(headline);
  });

  test("a normal headline is untouched", () => {
    const headline =
      "Nepal-India ties no longer defined by anti-India sentiment: FinMin Dr Wagle";
    expect(collapseRepeatedHeadline(headline)).toBe(headline);
  });

  test("a headline with a different ending is untouched", () => {
    const headline =
      "Minister says the bridge will open in March, and the bridge will open in March";
    expect(collapseRepeatedHeadline(headline)).toBe(headline);
  });

  test("empty and short values are safe", () => {
    expect(collapseRepeatedHeadline(null)).toBe("");
    expect(collapseRepeatedHeadline(undefined)).toBe("");
    expect(collapseRepeatedHeadline("Short")).toBe("Short");
  });

  test("a Nepali headline that repeats is collapsed too", () => {
    const doubled =
      "नेपालले आज पनि विदेशी मुद्राको सटोर बढाउने निर्णय गर्‍यो। नेपालले आज पनि विदेशी मुद्राको सटोर बढाउने निर्णय गर्‍यो।";
    expect(collapseRepeatedHeadline(doubled)).toBe(
      "नेपालले आज पनि विदेशी मुद्राको सटोर बढाउने निर्णय गर्‍यो।"
    );
  });
});

test.describe("a headline carrying the card's furniture", () => {
  test("the domain and the relative time go", () => {
    const cleaned = cleanHeadline(
      "gorkhapatraonline.com · ५५ मिनेट अघि खुदर्दुर्नामा एकको मृत्यु"
    );
    expect(cleaned).toBe("खुदर्दुर्नामा एकको मृत्यु");
  });

  test("the domain and the absolute date go", () => {
    const cleaned = cleanHeadline(
      "nagariknews.nagariknetwork.com · Fri Oct 23 16:10:16 GMT 2026 चार्ली कर्कोको मुद्दा"
    );
    expect(cleaned).not.toContain("nagariknews");
    expect(cleaned).not.toContain("GMT");
    expect(cleaned).toContain("चार्ली");
  });

  test("a clean headline is untouched", () => {
    const headline = "Nepal-India ties no longer defined by anti-India sentiment";
    expect(cleanHeadline(headline)).toBe(headline);
  });
});

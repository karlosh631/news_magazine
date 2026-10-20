import { expect, test } from "@playwright/test";

import {
  dropDuplicateStories,
  isSameStory,
  normalizeHeadline,
} from "../lib/feed-dedup";

/**
 * The duplicate-suppression contract.
 *
 * THE BUG THIS FILE EXISTS FOR
 * ---------------------------
 * The tokeniser used `[\p{L}\p{N}]+`, which does not match Devanagari
 * vowel signs — they are COMBINING marks, category Mc, not letters. So
 * every Nepali headline was shredded at each matra:
 *
 *     "शेयर बजार"  ->  "श यर बज र"
 *
 * `contentTokens` drops tokens of length 1, so most fragments were
 * discarded and a Nepali headline ended up with one or two usable
 * tokens. The matcher needs three shared tokens before it will compare
 * a pair at all, so it compared NOTHING: measured against 500 live
 * production articles, the dedup dropped ZERO before the fix.
 *
 * It also made false positives worse, because the surviving fragments
 * overlap heavily between unrelated headlines. "शेयर बजार उकालोमा"
 * (the market rose) and "शेयर बजार गिरावटमा" (the market fell) were
 * measured as the same story.
 *
 * Python's dedup.py was already correct. Only this file was broken.
 */

test.describe("Devanagari tokenisation", () => {
  test("a word keeps its vowel signs", () => {
    // The matras are combining marks, so a letter-only class splits
    // the word apart.
    expect(normalizeHeadline("शेयर बजार")).toBe("शेयर बजार");
    expect(normalizeHeadline("उकालोमा")).toBe("उकालोमा");
    expect(normalizeHeadline("गोरखा राज्यसम्म")).toBe("गोरखा राज्यसम्म");
  });

  test("punctuation stripping does not break words apart", () => {
    // The stripper runs BEFORE the tokeniser, so a matra treated as
    // punctuation becomes a space here and the word is split before
    // anything else sees it.
    expect(normalizeHeadline("काठमाडौँ, उपत्यकामा")).toBe("काठमाडौँ उपत्यकामा");
    expect(normalizeHeadline("दशैं—आँसुमा")).toBe("दशैं आँसुमा");
  });

  test("English is unaffected", () => {
    expect(normalizeHeadline("Nepal, Nepal!")).toBe("nepal nepal");
  });
});

test.describe("the same story from two outlets", () => {
  const SAME: [string, string][] = [
    // One outlet prefixed the location.
    ["शक्तिपीठमा भक्तजनको घुइँचो", "गुल्मीका शक्तिपीठमा भक्तजनको घुइँचो"],
    // A spelling difference in the middle of the word.
    [
      "भोटेकोशी बाढीपछि बर्दियाका पाँच परिवारको दशैं आँसुमा",
      "भोटेकोशी बाढीपछि बर्दियाका पाँच परिवारको दसैँ आँसुमा",
    ],
    // Punctuation differs.
    ["ट्रम्पको दाबी: पेरिस चाँडै मुस्लिम सहर बन्नेछ", "ट्रम्पको दाबी- पेरिस चाँडै मुस्लिम सहर बन्नेछ"],
    // The same story in English, with a tag appended.
    [
      "Nepal faces Oman in the Cricket World Cup League 2",
      "Nepal faces Oman in the Cricket World Cup League 2: Series preview",
    ],
  ];

  for (const [a, b] of SAME) {
    test(`${a.slice(0, 34)} == ${b.slice(0, 34)}`, () => {
      expect(isSameStory(a, b)).toBe(true);
    });
  }
});

test.describe("different stories that must stay apart", () => {
  const DIFFERENT: [string, string][] = [
    // The market rose vs the market fell. The same four words with one
    // different one — the case a similarity threshold alone gets wrong.
    ["शेयर बजार उकालोमा", "शेयर बजार गिरावटमा"],
    // Nepal beat Oman vs Nepal beat India.
    ["नेपालले ओमानलाई हराउन", "नेपालले भारतलाई हराउन"],
    // Entirely different topics that share ordinary words.
    [
      "प्रधानमन्त्रीले भारत भ्रमण गर्ने",
      "विरोधी दलले सरकारको विश्वास माग्यो",
    ],
    // Two different people, one story shape.
    [
      "प्रधानमन्त्री बलेन सहरका लागि स्वागत गरे",
      "प्रधानमन्त्री शाहले वाराण्डी सम्बोधन गरे",
    ],
  ];

  for (const [a, b] of DIFFERENT) {
    test(`${a.slice(0, 30)} != ${b.slice(0, 30)}`, () => {
      expect(isSameStory(a, b)).toBe(false);
    });
  }
});

test.describe("the feed keeps one card per story", () => {
  const row = (id: string, headline: string) => ({
    id,
    headline,
    source_count: 1,
    published_at: "2026-10-20T10:00:00Z",
    featured_image_url: null,
    image_highres_url: null,
  });

  test("a syndicated pair is collapsed to one", () => {
    const rows = [
      row("a", "शक्तिपीठमा भक्तजनको घुइँचो"),
      row("b", "गुल्मीका शक्तिपीठमा भक्तजनको घुइँचो"),
    ];
    expect(dropDuplicateStories(rows)).toHaveLength(1);
  });

  test("two different stories both survive", () => {
    const rows = [
      row("a", "शेयर बजार उकालोमा"),
      row("b", "शेयर बजार गिरावटमा"),
    ];
    expect(dropDuplicateStories(rows)).toHaveLength(2);
  });

  test("a Nepali feed is deduplicated at all", () => {
    // The regression that matters. Before the tokeniser fix this
    // dropped nothing, because no pair ever cleared the three-shared-
    // tokens gate: the fragments left after shredding were too short
    // and too few.
    const rows = [
      row("a", "शक्तिपीठमा भक्तजनको घुइँचो"),
      row("b", "गुल्मीका शक्तिपीठमा भक्तजनको घुइँचो"),
      row("c", "ट्रम्पको दाबी: पेरिस चाँडै मुस्लिम सहर बन्नेछ"),
      row("d", "ट्रम्पको दाबी- पेरिस चाँडै मुस्लिम सहर बन्नेछ"),
      row("e", "शेयर बजार उकालोमा"),
      row("f", "शेयर बजार गिरावटमा"),
    ];
    const kept = dropDuplicateStories(rows);
    expect(kept).toHaveLength(4);
    expect(kept.map((r) => r.id).sort()).toEqual(["a", "c", "e", "f"]);
  });

  test("order is never changed", () => {
    // The survivor keeps its own slot; a duplicate losing does not
    // promote anything above a newer story.
    const rows = [
      row("a", "शक्तिपीठमा भक्तजनको घुइँचो"),
      row("b", "गुल्मीका शक्तिपीठमा भक्तजनको घुइँचो"),
      row("c", "शेयर बजार उकालोमा"),
    ];
    const kept = dropDuplicateStories(rows);
    expect(kept.map((r) => r.id)).toEqual(["a", "c"]);
  });
});

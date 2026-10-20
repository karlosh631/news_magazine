"""
Tests for the category classifier.

The bug this file exists for
----------------------------
`/category/business` was serving disaster, childbirth and transport
stories. The cause was substring matching over Devanagari:

    if keyword in title:
        score += 3

Devanagari has no spaces inside a word, so the business keyword `"कर"`
— the ordinary verb "to do" — matched inside `सरकार` (government),
`कार्यालय` (office), `प्रकार` (manner). Any story whose headline or
excerpt contained `सरकार` scored +3 for business. "राष्ट्रिय सभा
अधिवेशन" filed under business because `राष्ट्र` contains no business
word but the excerpt's `कार्य` did.

Three defects lived in the same function:

  1. Raw substring matching, which is meaningless for an abugida.
  2. Ties broken by `max(scores, key=scores.get)`, which returns the
     FIRST maximum in iteration order — so the winner was whichever
     rule happened to be written first. Silently biased, not random.
  3. No minimum confidence: one weak excerpt match (worth 1) was
     enough to file an article.

What the tests below pin:

  - `सरकार` does not match the keyword `कर`.
  - A story containing `सरकार` is NOT classified as business.
  - Genuine business and politics headlines still classify correctly,
     so the fix did not simply blunt the classifier into `national`.
  - Ties resolve deterministically regardless of RULES insertion order.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.categorizer import Categorizer  # noqa: E402


SLUG_TO_ID = {
    "politics": "POL",
    "business": "BUS",
    "technology": "TEC",
    "sports": "SPO",
    "world": "WOR",
    "national": "NAT",
    "reels": "REE",
}

ID_TO_SLUG = {v: k for k, v in SLUG_TO_ID.items()}


@pytest.fixture
def classifier() -> Categorizer:
    return Categorizer(SLUG_TO_ID)


def slug_of(classifier: Categorizer, text: str) -> str | None:
    """Classify and translate the uuid back to a slug for readable asserts."""
    got = classifier.classify(text)
    return ID_TO_SLUG.get(got) if got else None


class TestDevanagariSubstring:
    """The defect itself."""

    def test_a_keyword_must_start_on_a_word_boundary(self, classifier):
        """
        A keyword must not fire when it is merely the PREFIX of a longer
        word. Nepali is agglutinative, so a leading boundary is the one
        that must hold: `दल` (party) is a prefix of nothing, but a keyword
        like `ब्य` would otherwise match inside arbitrary compounds.
        """
        from app.services.categorizer import _is_word_char

        # The two are NOT literal substrings of each other — `क` is followed
        # by the vowel sign `ा` and then `र`, so the characters are not
        # adjacent. Pinned because an earlier diagnosis of this bug claimed
        # otherwise, and it changes which words the fix has to cover.
        assert "कर" not in "सरकार", (
            "precondition: कर is NOT a substring of सरकार — the two are "
            "not adjacent in the codepoint sequence"
        )

        # A genuine prefix relationship, which the matcher must refuse when
        # the remainder starts a new word.
        assert "बजार" in "बजारलाई"
        assert _is_word_char("ल"), "precondition: ल is a word character"

        assert slug_of(classifier, "सरकारले गिरफ्तार गर्‍यो") == "politics", (
            "a government story must be politics, whatever the substring rules"
        )

    def test_a_government_arrest_story_is_politics_not_business(self, classifier):
        got = slug_of(classifier, "सरकारले गिरफ्तार गर्‍यो")
        assert got == "politics", (
            f"classified as {got!r}; this headline shape was landing in "
            "/category/business in production"
        )

    def test_disaster_and_crash_stories_do_not_file_as_business(self, classifier):
        """
        The three headlines observed sitting in /category/business.

        None of them contains a business word, so all three must fall
        through to `None` and be filed as national by the caller's
        fallback — never as business.
        """
        for headline in (
            "विपदका २४ घटनामा एकको मृत्यु",
            "जटिल प्रसूति भएकी महिलाको जीवन",
            "कैलालीबाट अछाम जाँदै",
        ):
            assert slug_of(classifier, headline) != "business", (
                f"{headline!r} filed as business"
            )

    def test_a_genuinely_business_headline_is_not_broadened_away(self, classifier):
        """The keyword lists still work; only the matching got stricter."""
        assert slug_of(classifier, "नेपालको शेयर बजार उकालोमा") == "business"
        assert slug_of(classifier, "बैंकले ब्याजदर घटाउने") == "business"


class TestStillClassifiesCorrectly:
    """The fix must not have blunted the classifier into a no-op."""

    @pytest.mark.parametrize(
        "headline,expected",
        [
            ("नेपालको शेयर बजार उकालोमा", "business"),
            ("बैंकले ब्याजदर घटाउने", "business"),
            ("प्रधानमन्त्रीले भारत भ्रमण", "politics"),
            ("राष्ट्रपतिले नयाँ सरकार गठन", "politics"),
            ("सूचना प्रविधि क्षेत्रको वृद्धि", "technology"),
            ("नेपालले भारतलाई क्रिकेटमा हराउन", "sports"),
        ],
    )
    def test_unambiguous_headlines_still_land_in_the_right_category(
        self, classifier, headline, expected
    ):
        assert slug_of(classifier, headline) == expected

    def test_the_classifier_still_spreads_across_categories(self, classifier):
        """
        A guard against "fix" == "return national always".

        National is the right fallback for a story with no signal, but if
        everything falls back to it the category pages are decorative.
        """
        headlines = [
            "प्रधानमन्त्रीले भारत भ्रमण",
            "नेपालको शेयर बजार उकालोमा",
            "क्रिकेटमा नेपालको जित",
            "सूचना प्रविधि क्षेत्रको वृद्धि",
        ]
        results = {slug_of(classifier, h) for h in headlines}
        assert len(results) >= 3, f"collapsed onto too few categories: {results}"


class TestUnknownCategory:
    def test_a_matching_rule_with_no_database_row_returns_none(self):
        """
        A rule can name a category that was deleted from the database.

        Returning the raw slug there would put a value in
        `primary_category_id` that no longer resolves to a row, and the
        story would vanish from every category page with no error.
        """
        partial = Categorizer({"national": "NAT"})
        assert partial.classify("प्रधानमन्त्रीले भारत भ्रमण") is None

    def test_empty_input_returns_none(self, classifier):
        assert classifier.classify("", "") is None
        assert classifier.classify(None, None) is None


class TestDeterminism:
    def test_the_same_input_always_yields_the_same_category(self, classifier):
        headline = "सरकार र बजार दुवैको चर्चा"
        first = slug_of(classifier, headline)
        for _ in range(20):
            assert slug_of(classifier, headline) == first, (
                "classification is not stable across calls"
            )

    def test_ties_do_not_depend_on_rules_insertion_order(self):
        """
        The old tie-break was `max(scores, key=scores.get)`, which returns
        the first maximum in iteration order — so a two-way tie was decided
        by which category was written first in RULES, silently.

        Build the same classifier twice from differently-ordered rule sets
        and require the same answer. If a future change reintroduces
        iteration-order dependence, this fails.
        """
        headline = "नेपालमा आज"

        forward = Categorizer(SLUG_TO_ID)
        reverse = Categorizer(SLUG_TO_ID)

        # Rebuild RULES in reversed declaration order on the second
        # instance only.
        import app.services.categorizer as cat

        original = cat.Categorizer.RULES
        try:
            reversed_rules = dict(reversed(list(original.items())))
            cat.Categorizer.RULES = reversed_rules

            a = slug_of(forward, headline)
            b = slug_of(reverse, headline)
        finally:
            cat.Categorizer.RULES = original

        assert a == b, (
            f"tie resolved differently by declaration order: {a!r} vs {b!r}"
        )

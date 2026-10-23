"""
A headline that says the same thing twice, said once.

THE BUG THIS EXISTS FOR
-----------------------
The search dropdown showed this:

    Nepal-India ties no longer defined by anti-India sentiment: FinMin
    Dr Wagle Nepal-India ties no longer defined by anti-India
    sentiment: FinMin Dr Wagle

The outlet's headline, with the outlet's own headline appended to it.
Some publishers put the headline in their markup twice - once as the
visible heading and once in a second element the desktop and the phone
each use - and the listing parser reads the container rather than the
heading.

The rule has to be strict. A headline that merely mentions itself is
news, not a bug.
"""

from __future__ import annotations

from app.services.sanitizer import collapse_repeated_headline, sanitize_headline

DOUBLED_EN = (
    "Tanker with Russian diesel heads to New York under Trump deal "
    "Tanker with Russian diesel heads to New York under Trump deal"
)
DOUBLED_NE = (
    "नेपालले आज पनि विदेशी मुद्राको सटोर बढाउने निर्णय गर्‍यो। "
    "नेपालले आज पनि विदेशी मुद्राको सटोर बढाउने निर्णय गर्‍यो।"
)


class TestCollapseRepeatedHeadline:
    def test_two_copies_become_one(self):
        assert collapse_repeated_headline(DOUBLED_EN) == (
            "Tanker with Russian diesel heads to New York under Trump deal"
        )

    def test_a_nepali_headline_that_repeats_is_collapsed_too(self):
        assert collapse_repeated_headline(DOUBLED_NE) == (
            "नेपालले आज पनि विदेशी मुद्राको सटोर बढाउने निर्णय गर्‍यो।"
        )

    def test_three_copies_become_one(self):
        one = "Tanker with Russian diesel heads to New York under Trump deal"
        assert collapse_repeated_headline(f"{one} {one} {one}") == one

    def test_a_headline_that_merely_mentions_itself_is_left_alone(self):
        headline = "Man arrested after man arrested in a theft case"
        assert collapse_repeated_headline(headline) == headline

    def test_a_normal_headline_is_untouched(self):
        headline = (
            "Nepal-India ties no longer defined by anti-India sentiment: "
            "FinMin Dr Wagle"
        )
        assert collapse_repeated_headline(headline) == headline

    def test_a_headline_with_a_different_ending_is_untouched(self):
        headline = (
            "Minister says the bridge will open in March, and the bridge "
            "will open in March"
        )
        assert collapse_repeated_headline(headline) == headline

    def test_short_and_empty_values_are_safe(self):
        assert collapse_repeated_headline("") == ""
        assert collapse_repeated_headline("Short") == "Short"


class TestSanitizeHeadline:
    def test_the_sanitizer_collapses_a_repeated_headline(self):
        assert sanitize_headline(DOUBLED_EN) == (
            "Tanker with Russian diesel heads to New York under Trump deal"
        )

    def test_the_sanitizer_still_strips_html(self):
        assert sanitize_headline("<b>Hello</b> world") == "Hello world"

    def test_the_sanitizer_still_trims_to_the_length_limit(self):
        assert len(sanitize_headline("x" * 900)) <= 500

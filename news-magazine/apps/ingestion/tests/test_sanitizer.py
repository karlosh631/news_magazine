"""
A headline that says the same thing twice, said once — and a listing
card's furniture taken out of the story's own words.

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

And the same card's furniture - the outlet's domain, the date and the
time - was ending up in the story's four key points:

    नयाँ संस्कारको नागरिक ... himalpress.com • Fri Oct 23 16:05:51
    GMT 2026 काठमाडौं

None of that is a sentence anybody wrote.

The rules have to be strict. A headline that merely mentions itself is
news, not a bug, and a story that mentions a website in its own words
keeps it.
"""

from __future__ import annotations

from app.services.sanitizer import (
    collapse_repeated_headline,
    sanitize_headline,
    strip_card_chrome,
)

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


class TestStripCardChrome:
    """
    The listing card's furniture is not part of any sentence.
    """

    def test_the_domain_and_the_date_go(self):
        cleaned = strip_card_chrome(
            "Meta CEO Mark Zuckerberg to Receive Additional Shares Amidst "
            "California Tax Proposal english.ratopati.com • Fri Oct 23 "
            "15:54:57 GMT 2026"
        )
        assert "ratopati.com" not in cleaned
        assert "GMT" not in cleaned
        assert "Meta CEO Mark Zuckerberg" in cleaned

    def test_the_nepali_shape_goes(self):
        cleaned = strip_card_chrome(
            "नेपालnews.com • Fri Oct 23 15:32:52 GMT 2026"
        )
        assert "news.com" not in cleaned
        assert "GMT" not in cleaned

    def test_the_relative_time_goes(self):
        cleaned = strip_card_chrome(
            "Oman and America to talk in Abu Dhabi 18 minutes ago"
        )
        assert "18 minutes ago" not in cleaned
        assert "Abu Dhabi" in cleaned

    def test_a_sentence_that_mentions_a_website_keeps_it(self):
        text = "The ministry said the notice on nepalnews.com will be published."
        assert strip_card_chrome(text) == text

    def test_plain_text_is_untouched(self):
        text = "Oman, Russia and America to talk in Abu Dhabi."
        assert strip_card_chrome(text) == text

    def test_empty_is_safe(self):
        assert strip_card_chrome(None) == ""
        assert strip_card_chrome("") == ""

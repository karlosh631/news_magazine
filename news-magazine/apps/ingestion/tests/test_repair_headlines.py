"""
The repair sweep must spot a listing card that was mistaken for a
headline — and must not touch a real one.

THE ROWS THIS EXISTS FOR
------------------------
The reader saw this on the site, as the headline of a real story on a
real Nepali outlet:

    canadanepal.com . 33 minutes ago Cyber security risks ... |
    The web technology (PHP) ...

The listing card is the publisher's own summary of the story, and on a
client-rendered listing that summary is the WHOLE card: the outlet's
domain, the time since publication, the headline, the date and the
first paragraph, in one string.

The sweep repairs the headline, the four points and the picture from
the story's OWN page. These specs pin the DETECTION, because a sweep
that mistakes a real headline for a card would rewrite a good row for
no reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.repair_headlines_and_pictures import (  # noqa: E402
    looks_like_a_card,
    points_look_like_a_card,
)


class TestLooksLikeACard:
    def test_a_card_with_the_outlets_domain_is_a_card(self):
        assert looks_like_a_card(
            "canadanepal.com . 33 minutes ago Cyber security risks ... | The web "
            "technology (PHP) is putting government websites at risk"
        )

    def test_a_card_with_the_relative_time_is_a_card(self):
        assert looks_like_a_card(
            "nepalnews.com . 18 minutes ago Oman, Russia and America to talk in "
            "Abu Dhabi"
        )

    def test_a_card_in_nepali_is_a_card(self):
        assert looks_like_a_card(
            "नेपालnews.com . १८ मिनेट अघि रुच, चुक्र र अमेरिकाबीच अबुधाबीमा "
            "वार्ता हुने"
        )

    def test_a_card_with_the_absolute_date_is_a_card(self):
        assert looks_like_a_card(
            "रुच, चुक्र र अमेरिकाबीच अबुधाबीमा वार्ता हुने nepalnews.com • "
            "Fri Oct 23 15:32:52 GMT 2026 काठमाडौं"
        )

    def test_a_real_headline_is_not_a_card(self):
        assert not looks_like_a_card(
            "Nepal-India ties no longer defined by anti-India sentiment: FinMin "
            "Dr Wagle"
        )

    def test_a_real_nepali_headline_is_not_a_card(self):
        assert not looks_like_a_card("रुच, चुक्र र अमेरिकाबीच अबुधाबीमा वार्ता हुने")

    def test_a_headline_mentioning_a_country_is_not_a_card(self):
        # "com.np" inside a word, and a domain-like phrase in the story
        # itself, must not make the row a suspect.
        assert not looks_like_a_card(
            "Nepal's exports to India rose 12 per cent, says the trade ministry"
        )

    def test_nothing_is_not_a_card(self):
        assert not looks_like_a_card(None)
        assert not looks_like_a_card("")
        assert not looks_like_a_card("   ")

    def test_a_very_long_headline_is_a_card(self):
        # A headline that runs to several hundred characters is not a
        # headline; it is a card.
        assert looks_like_a_card("word " * 60)


class TestPointsLookLikeACard:
    def test_points_carrying_the_domain_are_a_card(self):
        assert points_look_like_a_card(
            ["रुच, चुक्र र अमेरिकाबीच अबुधाबीमा वार्ता हुने nepalnews.com"]
        )

    def test_points_carrying_the_date_are_a_card(self):
        assert points_look_like_a_card(
            ["Oman, Russia and America to talk in Abu Dhabi Fri Oct 23 15:32:52 GMT 2026"]
        )

    def test_clean_points_are_not_a_card(self):
        assert not points_look_like_a_card(
            [
                "रुच, चुक्र र अमेरिकाबीच अबुधाबीमा वार्ता हुने",
                "The talks are expected to focus on the war.",
            ]
        )

    def test_no_points_are_not_a_card(self):
        assert not points_look_like_a_card(None)
        assert not points_look_like_a_card([])

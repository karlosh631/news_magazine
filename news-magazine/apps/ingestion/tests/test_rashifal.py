"""
The twelve-sign rashifal: today's horoscope for each sign.

WHAT THESE TESTS PIN
--------------------
The parser is the only part of this feature that can be wrong
quietly. A page that parses into a plausible-looking row with the
lucky colour in the lucky number column, or with the signs in the
wrong order, would show a confidently wrong horoscope on the
category page and nothing would report an error.

So the fixture below is a SAVED RESPONSE, not an invented one: the
twelve signs are exactly what hamropatro.com served on 2026-10-22
(Kartik 5, 2083 BS), copied out of the live page.

The cases are the three that can happen:

  1. a normal day - everything present.
  2. a day with a missing optional field - a sign with no advice, a
     sign with no emoji, a lucky sentence the parser does not know.
  3. a malformed response - not a page at all.

THE THREE THINGS THAT ARE EASY TO GET WRONG
--------------------------------------------
1. THE LUCKY COLOUR AND NUMBER ARE NOT STRUCTURED. They are the
   last sentence of the advice ("आजको शुभ रंग गुलाबी हो भने शुभ
   अंक ७ रहेको छ।") and they have to be read out of it. The site
   is not consistent about the spacing around that sentence, and one
   sign has a space before it and another has two after the full
   stop.

2. THE ADVICE OPENS WITH A ZERO-WIDTH SPACE. Left in, it is the
   first character of every advice string in the database and it
   renders as a stray gap before the first word.

3. THE SIGN NUMBER IS THE POSITION. It is what a reader sorts by,
   so it has to mean मेष = 1 ... मीन = 12, and the parser warns
   when the order it was handed is not the order it expected.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, time

import pytest

import app.services.panchang as panchang_module

from app.services.panchang import (
    forget_last_stored_bs,
    last_stored_signs_bs,
    sync_panchang,
)


# =============================================================
# THE CLOCK IS PINNED
#
# The pass asks the DATABASE whether today's row is there, and
# today's row is keyed by the NEPAL date - so the check is made
# against today's real date, not against the day the fixture was
# saved.
#
# That made these tests fail THE DAY AFTER they were written: the
# fixture's day is 2026-10-22, and on 2026-10-23 the row it stores
# is in the past, the freshness check finds nothing, and the pass
# does the work again. A test that breaks the day after it is
# written is a broken test, so the clock is pinned to the fixture's
# day here and the fixture never ages.
# =============================================================


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch):
    monkeypatch.setattr(
        panchang_module, "nepal_today", lambda *a, **k: date(2026, 10, 22)
    )
from app.services.rashifal import (
    RASHIFAL_CONFLICT,
    RashifalParseError,
    parse_rashifal,
    rashifal_row,
    same_horoscope,
    store_rashifal,
)


def run(coro):
    # asyncio.run(), not get_event_loop().run_until_complete().
    # get_event_loop() is deprecated and raises "no current event
    # loop" once another test has closed the loop it created, which
    # makes this helper order-dependent.
    return asyncio.run(coro)


# =============================================================
# THE SAVED FIXTURE
#
# The twelve signs, copied out of the live page on 2026-10-22.
# =============================================================

RASHIFAL_SIGNS = [
    {
        "slug": "mesh",
        "np": "मेष",
        "en": "Aries",
        "emoji": "♈",
        "initials": "चु, चे, चो, ला, लि, लु, ले, लो, अ",
        "descNp": "​अचानक भेटघाट हुने मानिसबाट महत्त्वपूर्ण व्यावसायिक सल्लाह पाउन सकिनेछ। घरको पुराना सामान व्यवस्थापन गर्ने काममा समय बित्नेछ। आफन्तसँगको सानो यात्राले मनलाई आनन्दित तुल्याउनेछ। आजको शुभ रंग गुलाबी हो भने शुभ अंक ७ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "brish",
        "np": "वृष",
        "en": "Taurus",
        "emoji": "♉",
        "initials": "इ, उ, ए, ओ, वा, वि, वु, वे, वो",
        "descNp": "​अध्ययन तथा प्राविधिक क्षेत्रमा देखिएका जटिलताहरू सहज रूपमा हल हुनेछन्। पुराना साथीसँग भएको कुराकानीले नयाँ सोच र ऊर्जा थप्नेछ। साँझको समयमा परिवारसँग रमाइलो भोजन गर्ने अवसर जुरेको छ। आजको शुभ रंग आकाशी हो भने शुभ अंक ४ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "mithun",
        "np": "मिथुन",
        "en": "Gemini",
        "emoji": "♊",
        "initials": "का, कि, कु, घ, ङ, छ, के, को, हा",
        "descNp": "​व्यापारिक कामको सिलसिलामा छोटो तर फलदायी यात्रा तय हुनेछ। आफ्नो सिप र कला देखाएर अरूको मन जित्न सफल भइनेछ। कसैको लहलहैमा लागेर ठूलो निर्णय लिनबाट टाढै बस्नुहोला। आजको शुभ रंग पहेंलो हो भने शुभ अंक ९ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "karkat",
        "np": "कर्कट",
        "en": "Cancer",
        "emoji": "♋",
        "initials": "हि, हु, हे, हो, डा, डि, डु, डे, डो",
        "descNp": "​बोलीमा मिठास ल्याउँदा बिग्रन लागेका कामहरू पनि सजिलै बन्नेछन्। पैतृक सम्पत्ति वा जग्गासम्बन्धी विषयमा घरका मान्यजनसँग छलफल हुनेछ। मनमा सकारात्मक सोचले बास गर्दा दिन उत्साहप्रद बन्नेछ। आजको शुभ रंग खैरो हो भने शुभ अंक २ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "singha",
        "np": "सिंह",
        "en": "Leo",
        "emoji": "♌",
        "initials": "मा, मि, मु, मे, मो, टा, टि, टु, टे",
        "descNp": "​आत्मविश्वास बलियो हुनाले रोकिएका महत्त्वपूर्ण जिम्मेवारीहरू पूरा हुनेछन्। प्रेम तथा दाम्पत्य जीवनमा एकअर्काप्रतिको समर्पण भाव अझ बढेर जानेछ। नयाँ वस्त्र वा मनपर्ने सामाग्री खरिद गर्ने योजना बन्न सक्छ। आजको शुभ रंग बैजनी हो भने शुभ अंक ३ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "kanya",
        "np": "कन्या",
        "en": "Virgo",
        "emoji": "♍",
        "initials": "टो, पा, पि, पु, ष, ण, ठ, पे, पो",
        "descNp": "​खर्च बढे पनि आवश्यक ठाउँमा धन लगानी गर्नुपर्ने अवस्था आउन सक्छ। टाढाको यात्रा वा वैदेशिक क्षेत्रबाट कुनै सुखद खबर सुन्न पाइनेछ। परोपकार वा समाजसेवाका काममा मन जानेछ। आजको शुभ रंग सेतो हो भने शुभ अंक ८ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "tula",
        "np": "तुला",
        "en": "Libra",
        "emoji": "♎",
        "initials": "रा, रि, रु, रे, रो, ता, ति, तु, ते",
        "descNp": "​साथीभाइको संयुक्त प्रयासबाट कुनै ठुलो काम सम्पन्न गर्न सकिनेछ। लामो समयदेखि रोकिएको आर्थिक योजनाहरूले गति लिनेछन्। आफ्नो स्वास्थ्यलाई प्राथमिकतामा राखेर समय मिलाउनुहोला। आजको शुभ रंग सुन्तला हो भने शुभ अंक ५ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "brischik",
        "np": "वृश्चिक",
        "en": "Scorpio",
        "emoji": "♏",
        "initials": "तो, ना, नि, नु, ने, नो, या, यि, यु",
        "descNp": "​कार्यक्षेत्रमा नयाँ अधिकार वा पदको जिम्मेवारी थपिन सक्छ। घरपरिवारको कुनै सदस्यको स्वास्थ्यलाई लिएर केही चिन्ता बढ्न सक्छ। आफ्नो गोपनीयतामा ध्यान दिँदै काम गर्नुहोला। आजको शुभ रंग निलो हो भने शुभ अंक १ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "dhanu",
        "np": "धनु",
        "en": "Sagittarius",
        "emoji": "♐",
        "initials": "ये, यो, भा, भि, भु, धा, फा, ढा, भे",
        "descNp": "​दाजुभाइ वा इष्टमित्रको सहयोगले अप्रत्याश्यो परिस्थिति तरेर जानेछ। उच्च शिक्षाका लागि गरेका प्रयासहरूमा सफलता मिल्नेछ। साँझपख मनपर्ने संगीत वा पुस्तकमा समय बित्नेछ। आजको शुभ रंग सिन्दूरी हो भने शुभ अंक ६ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "makar",
        "np": "मकर",
        "en": "Capricorn",
        "emoji": "♑",
        "initials": "भो,जा,जि,जु,जे,जो,ख,खि,खु,खे,खो,गा,गि",
        "descNp": "​गोप्य रूपमा रिएका कामहरूबाट अप्रत्याशित सफलता हात लाग्नेछ। सवारी साधन चलाउँदा वा बाटो काट्दा विशेष सावधानी अपनाउनुहोला। मनको शान्तिका लागि केही समय ध्यान गर्नुपर्छ। आजको शुभ रंग रातो हो भने शुभ अंक ४ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "kumbha",
        "np": "कुम्भ",
        "en": "Aquarius",
        "emoji": "♒",
        "initials": "गु, गे, गो, सा, सि, सु, से, सो, दा",
        "descNp": "​साझेदारीको व्यापारमा देखिएका पुराना विवादहरू आपसी समझदारीमा टुंगिनेछ। जीवनसाथीसँगको सानो रमाइलो घुम्घामले सम्बन्धमा नयाँ मिठास ल्याउनेछ। सामाजिक क्षेत्रमा नेतृत्व पाउने अवसर प्राप्त हुन सक्छ।  आजको शुभ रंग हरियो हो भने शुभ अंक २ रहेको छ।",
        "descEn": ""
    },
    {
        "slug": "meen",
        "np": "मीन",
        "en": "Pisces",
        "emoji": "♓",
        "initials": "दि, दु, थ, झ, ञ, दे, दो, चा, चि",
        "descNp": "​प्रतियोगितात्मक काममा आफ्नै मेहनतले उच्च स्थान हासिल गर्न सकिनेछ। पुराना ऋण वा आर्थिक दायित्वहरू चुक्ता गर्न सहज वातावरण बन्नेछ। विद्यार्थीहरूले परीक्षामा उत्कृष्ट परिणाम हात पार्नेछन्। आजको शुभ रंग कलेजी हो भने शुभ अंक ९ रहेको छ।",
        "descEn": ""
    },
]

#: The site's "today" block, which every page of the site carries.
TODAY_BLOCK = {
    "year_ad": 2026,
    "month_ad": 10,
    "date_ad": 22,
    "year_bs": 2083,
    "month_bs": 7,
    "date_bs": 5,
    "day": 5,
    "month_ad_en": "October",
    "month_ad_np": "अक्टोबर",
    "month_bs_en": "Kartik",
    "month_bs_np": "कार्तिक",
    "date_bs_np": "५",
    "day_en": "Thursday",
    "day_np": "बिहिवार",
    "adKey": "2026-10-22",
    "bsFormatted": "कार्तिक ५, २०८३",
    "adFormatted": "22 October 2026",
    "weekdayNp": "बिहिवार",
    "weekdayEn": "Thursday",
}

#: The smallest per-day block the panchang parser will accept. The
#: rashifal tests need a day page for the pass, not a whole one.
MINIMAL_DETAIL = {
    "year_ad": 2026,
    "month_ad": 10,
    "day_ad": 22,
    "year_bs": 2083,
    "month_bs": 7,
    "day_bs": 5,
    "tithiShort": "एकादशी",
    "panchanga": {"sunrise": 22061, "sunset": 62913, "paksha": 0},
    "weekdayNp": "बिहिवार",
    "weekdayEn": "Thursday",
    "monthNp": "कार्तिक",
    "monthEn": "Kartik",
    "yearBsNp": "२०८३",
    "dayBsNp": "५",
    "panchangaText": {
        "tithi": "असोज शुक्ल एकादशी",
        "yoga": "वृद्धि",
        "karana": "विष्टि/भद्रा",
        "nakshatra": "शतभिषा",
    },
    "sunriseNp": "०६:०७",
    "sunsetNp": "१७:२८",
}


def _page(
    signs: list | None = None,
    today: dict | None = None,
    detail: dict | None = None,
    *,
    raw_bootstrap: str | None = None,
) -> str:
    """Stitch a saved fixture back into the shape the site serves."""
    payload = {
        "path": "/rashifal",
        "locale": "np",
        "page": "rashifal-index",
        "today": today if today is not None else TODAY_BLOCK,
        "data": {
            "rashifal": {
                "date": "०५ कार्तिक  २०८३ बिहिवार",
                "signs": signs if signs is not None else RASHIFAL_SIGNS,
            },
        },
    }
    if detail is not None:
        payload["data"]["detail"] = detail
        payload["page"] = "date"

    body = raw_bootstrap if raw_bootstrap is not None else json.dumps(
        payload, ensure_ascii=False
    )

    return (
        "<!DOCTYPE html><html lang='ne'><head><title>Hamro Patro</title></head>"
        "<body>"
        f"<script id=\"hp-bootstrap\" type=\"application/json\">{body}</script>"
        "</body></html>"
    )


NORMAL_DAY_HTML = _page()

#: The same day with the optional parts of two signs left out: one
#: with no advice at all, one with no emoji and no initials.
_missing = json.loads(json.dumps(RASHIFAL_SIGNS))
_missing[2].pop("descNp", None)
_missing[5].pop("emoji", None)
_missing[5].pop("initials", None)
MISSING_OPTIONAL_HTML = _page(signs=_missing)

#: A page with one sign fewer than the zodiac has.
_short = json.loads(json.dumps(RASHIFAL_SIGNS))[:-1]
SHORT_HTML = _page(signs=_short)

#: Not a page at all.
MALFORMED_HTML = (
    "<!DOCTYPE html><html><head><title>Hamro Patro</title></head>"
    "<body><h1>Something went wrong</h1></body></html>"
)

#: A page whose JSON island is not JSON.
BROKEN_JSON_HTML = _page(raw_bootstrap="{not json at all")

#: A page with no rashifal block at all.
NO_RASHIFAL_HTML = _page(signs=[])


# =============================================================
# THE NORMAL DAY
# =============================================================


def test_the_twelve_signs_are_parsed_in_order():
    """
    Twelve signs, in the zodiac order, numbered 1 to 12. The number
    is what a reader sorts by, so it has to be the position.
    """
    signs = parse_rashifal(NORMAL_DAY_HTML)

    assert len(signs) == 12
    assert [sign.sign for sign in signs] == list(range(1, 13))
    assert [sign.slug for sign in signs] == [
        "mesh", "brish", "mithun", "karkat", "singha", "kanya",
        "tula", "brischik", "dhanu", "makar", "kumbha", "meen",
    ]


def test_a_sign_carries_its_name_in_both_languages():
    """
    The site publishes the name in Nepali and in English, and a
    Nepali site shows the Nepali one.
    """
    signs = parse_rashifal(NORMAL_DAY_HTML)

    assert signs[0].name_ne == "मेष"
    assert signs[0].name == "Aries"
    assert signs[0].emoji == "♈"
    assert signs[11].name_ne == "मीन"
    assert signs[11].name == "Pisces"


def test_a_sign_carries_the_letter_initials():
    """The letters the site prints under the sign's name."""
    signs = parse_rashifal(NORMAL_DAY_HTML)

    assert signs[0].initials == "चु, चे, चो, ला, लि, लु, ले, लो, अ"


def test_a_sign_carries_the_advice():
    signs = parse_rashifal(NORMAL_DAY_HTML)

    assert signs[0].advice.startswith("अचानक भेटघाट हुने मानिसबाट")
    assert "आफन्तसँगको सानो यात्राले मनलाई आनन्दित तुल्याउनेछ।" in signs[0].advice


def test_the_advice_has_no_zero_width_characters():
    """
    The published advice opens with a zero-width space. Left in, it
    is the first character of every advice string in the database
    and it renders as a stray gap before the first word.
    """
    signs = parse_rashifal(NORMAL_DAY_HTML)

    for sign in signs:
        assert sign.advice is not None
        # U+200B, U+200C, U+200D and U+FEFF, written as numbers so
        # that they cannot be lost by an editor that does not show
        # them.
        for code in (0x200B, 0x200C, 0x200D, 0xFEFF):
            assert chr(code) not in sign.advice, sign.slug
        assert not sign.advice.startswith(" "), sign.slug


def test_the_lucky_colour_and_number_are_read_out_of_the_advice():
    """
    They are not structured data - they are the last sentence of the
    advice, and they have to be read out of it.
    """
    signs = parse_rashifal(NORMAL_DAY_HTML)

    assert (signs[0].lucky_colour, signs[0].lucky_number) == ("गुलाबी", "७")
    assert (signs[1].lucky_colour, signs[1].lucky_number) == ("आकाशी", "४")
    assert (signs[10].lucky_colour, signs[10].lucky_number) == ("हरियो", "२")
    assert (signs[11].lucky_colour, signs[11].lucky_number) == ("कलेजी", "९")


def test_the_lucky_sentence_is_read_whatever_the_spacing_around_it():
    """
    The site is not consistent about the spacing around that
    sentence. One sign has a space before it and another has two
    after the full stop, and both have to be read.
    """
    signs = parse_rashifal(NORMAL_DAY_HTML)

    # मिथुन's advice ends "...लागेर ठूलो निर्णय लिनबाट टाढै बस्नुहोला। आजको..."
    # and कुम्भ's ends "...हुन सक्छ।  आजको..." - two spaces.
    assert signs[2].lucky_colour == "पहेंलो"
    assert signs[10].lucky_colour == "हरियो"


def test_the_lucky_number_stays_in_nepali_numerals():
    """
    The site prints the number in Nepali numerals, and that is what
    a Nepali reader expects to see. Converting it to ASCII here
    would mean the frontend had to know to convert it back.
    """
    signs = parse_rashifal(NORMAL_DAY_HTML)

    assert signs[0].lucky_number == "७"
    assert signs[7].lucky_number == "१"


# =============================================================
# A DAY WITH A MISSING OPTIONAL FIELD
# =============================================================


def test_a_sign_with_no_advice_still_parses():
    """
    A sign with no advice is a sign with an empty advice column.
    It must not take the other eleven with it.
    """
    signs = parse_rashifal(MISSING_OPTIONAL_HTML)

    assert len(signs) == 12
    assert signs[2].advice is None
    assert signs[2].lucky_colour is None
    assert signs[2].lucky_number is None
    assert signs[2].name_ne == "मिथुन"
    # And the other eleven are untouched.
    assert signs[0].advice is not None
    assert signs[11].advice is not None


def test_a_sign_with_no_emoji_or_initials_still_parses():
    signs = parse_rashifal(MISSING_OPTIONAL_HTML)

    assert signs[5].emoji is None
    assert signs[5].initials is None
    assert signs[5].name_ne == "कन्या"
    assert signs[5].advice is not None


def test_a_lucky_sentence_the_parser_does_not_know_leaves_the_colour_empty():
    """
    A wording this parser does not recognise must leave the colour
    and the number empty. A guess would be printed as fact.
    """
    html = _page(
        signs=[
            dict(
                RASHIFAL_SIGNS[0],
                descNp="आजको शुभ रंग नयाँ शब्दमा हुनुहुन्छ।",
            )
        ]
    )

    signs = parse_rashifal(html)

    assert signs[0].lucky_colour is None
    assert signs[0].lucky_number is None
    # And the advice is still there, so nothing is lost.
    assert signs[0].advice == "आजको शुभ रंग नयाँ शब्दमा हुनुहुन्छ।"


def test_a_page_with_one_sign_short_is_still_parsed():
    """
    Eleven signs is not twelve, and the parser says so in the log
    rather than refusing the page. A page that has lost a sign is a
    problem to see, not a reason to lose the other eleven.
    """
    signs = parse_rashifal(SHORT_HTML)

    assert len(signs) == 11
    assert signs[10].slug == "kumbha"


# =============================================================
# A MALFORMED RESPONSE
# =============================================================


def test_a_malformed_response_is_rejected():
    with pytest.raises(RashifalParseError):
        parse_rashifal(MALFORMED_HTML)


def test_a_response_whose_json_is_broken_is_rejected():
    with pytest.raises(RashifalParseError):
        parse_rashifal(BROKEN_JSON_HTML)


def test_a_response_with_no_rashifal_block_is_rejected():
    with pytest.raises(RashifalParseError):
        parse_rashifal(NO_RASHIFAL_HTML)


# =============================================================
# THE ROW
# =============================================================


def test_the_row_carries_the_sign_and_the_day():
    """
    The row is one sign on one day, and the two together are the
    primary key.
    """
    sign = parse_rashifal(NORMAL_DAY_HTML)[0]
    row = rashifal_row(sign, date(2026, 10, 22), "https://hamropatro.com/rashifal")

    assert row["panchang_date"] == "2026-10-22"
    assert row["sign"] == 1
    assert row["sign_slug"] == "mesh"
    assert row["sign_name"] == "Aries"
    assert row["sign_name_ne"] == "मेष"
    assert RASHIFAL_CONFLICT == "panchang_date,sign"


def test_the_row_carries_the_advice_and_the_lucky_colour_and_number():
    sign = parse_rashifal(NORMAL_DAY_HTML)[0]
    row = rashifal_row(sign, date(2026, 10, 22), "https://hamropatro.com/rashifal")

    assert row["advice"].startswith("अचानक भेटघाट")
    assert row["lucky_colour"] == "गुलाबी"
    assert row["lucky_number"] == "७"


def test_the_row_always_writes_every_column():
    """
    Every key is written on every pass, `updated_at` included.

    A bulk upsert - one POST for all twelve rows - leaves the
    columns a row does not mention NULL rather than falling back to
    the column default, so a column left out of the payload is a
    column that gets wiped. `updated_at` is NOT NULL with a default,
    and NULL there is a failed write.
    """
    sign = parse_rashifal(NORMAL_DAY_HTML)[0]
    row = rashifal_row(sign, date(2026, 10, 22), "https://hamropatro.com/rashifal")

    for column in (
        "panchang_date", "sign", "sign_slug", "sign_name", "sign_name_ne",
        "sign_emoji", "initials", "advice", "lucky_colour", "lucky_number",
        "source_url", "fetched_at", "updated_at",
    ):
        assert column in row, column

    assert row["updated_at"] is not None


def test_a_missing_optional_field_is_stored_as_null_not_as_an_empty_string():
    """
    NULL and "" are different answers and the frontend has to be able
    to tell them apart.
    """
    signs = parse_rashifal(MISSING_OPTIONAL_HTML)
    row = rashifal_row(signs[2], date(2026, 10, 22), "https://hamropatro.com/rashifal")

    assert row["advice"] is None
    assert row["lucky_colour"] is None
    assert row["lucky_number"] is None


# =============================================================
# STORING
# =============================================================


class FakeData:
    """A supabase-py response, with the rows the fake kept."""

    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, recorder, table):
        self._recorder = recorder
        self._table = table
        self._filters: list[tuple[str, str, str]] = []
        self._mode = "write"
        self._limit: int | None = None

    def upsert(self, row, on_conflict=None):
        self._recorder["upserts"].append((self._table, row, on_conflict))
        # A real upsert leaves the row in the table, and the pass
        # reads the table back to decide whether it needs to fetch
        # anything. So the fake keeps it too.
        self._recorder.setdefault("stored", {}).setdefault(
            self._table, []
        ).extend(row if isinstance(row, list) else [row])
        return self

    def select(self, columns):
        self._mode = "read"
        return self

    def gte(self, column, value):
        self._filters.append(("gte", column, str(value)))
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, str(value)))
        return self

    def maybe_single(self):
        self._mode = "single"
        return self

    def limit(self, count):
        self._limit = count
        return self

    def execute(self):
        if self._mode == "write":
            return self._recorder

        rows = list(self._recorder.get("stored", {}).get(self._table, []))
        for kind, column, value in self._filters:
            if kind == "gte":
                rows = [
                    row for row in rows
                    if str(row.get(column, "")) >= value
                ]
            else:
                rows = [
                    row for row in rows
                    if str(row.get(column, "")) == value
                ]

        if self._mode == "single":
            return FakeData(rows[0] if rows else None)

        limit = getattr(self, "_limit", None)
        if limit is not None:
            rows = rows[:limit]
        return FakeData(rows)


class FakeDB:
    """A supabase-py stand-in that records what it was asked to write."""

    def __init__(self):
        self.upserts: list[tuple] = []
        self.stored: dict[str, list[dict]] = {}
        self.recorder = {"upserts": self.upserts, "stored": self.stored}

    def table(self, name):
        return FakeQuery(self.recorder, name)


class ExplodingDB:
    def table(self, name):
        raise RuntimeError("the database is unreachable")


def test_the_twelve_signs_are_written_in_one_upsert():
    """
    All twelve go in one request, and they go in as an upsert on
    (panchang_date, sign) so a second pass refreshes them instead of
    adding a second set.
    """
    db = FakeDB()
    signs = parse_rashifal(NORMAL_DAY_HTML)

    assert run(store_rashifal(db, signs, date(2026, 10, 22), "https://x")) is True
    assert len(db.upserts) == 1

    table, rows, on_conflict = db.upserts[0]
    assert table == "daily_rashifal"
    assert on_conflict == "panchang_date,sign"
    assert isinstance(rows, list)
    assert len(rows) == 12
    assert [row["sign"] for row in rows] == list(range(1, 13))
    assert all(row["panchang_date"] == "2026-10-22" for row in rows)


def test_a_failed_write_is_reported_and_not_raised():
    """
    A missing horoscope is not a reason to take the worker down.
    """
    signs = parse_rashifal(NORMAL_DAY_HTML)

    assert run(store_rashifal(ExplodingDB(), signs, date(2026, 10, 22), "https://x")) is False


def test_storing_nothing_is_not_a_success():
    signs = parse_rashifal(NORMAL_DAY_HTML)
    db = FakeDB()

    assert run(store_rashifal(db, [], date(2026, 10, 22), "https://x")) is False
    assert db.upserts == []


# =============================================================
# THE PASS
# =============================================================


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RecordingClient:
    """An httpx.AsyncClient stand-in that remembers what it was asked."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[str] = []

    async def get(self, url):
        self.calls.append(url)
        if url not in self.responses:
            raise RuntimeError(f"no fixture for {url}")
        return FakeResponse(self.responses[url])

    async def aclose(self):
        pass


def _client() -> RecordingClient:
    return RecordingClient(
        {
            "https://hamropatro.com/rashifal": NORMAL_DAY_HTML,
            "https://hamropatro.com/date/2083-7-5": _page(detail=MINIMAL_DETAIL),
        }
    )


def test_the_pass_stores_the_horoscopes_out_of_the_page_it_already_fetched():
    """
    The twelve signs ride along in the response the pass fetches to
    learn what day it is, so they cost no request of their own.
    """
    forget_last_stored_bs()
    client = _client()
    db = FakeDB()

    run(sync_panchang(db, client=client))

    rashifal_writes = [
        write for write in db.upserts if write[0] == "daily_rashifal"
    ]
    assert len(rashifal_writes) == 1
    assert len(rashifal_writes[0][1]) == 12


def test_the_pass_asks_for_the_rashifal_page_only_once():
    """
    There is no second request for the horoscopes. The page that
    carries today's date also carries the signs, and the pass asks
    for that page and nothing else to do with them.
    """
    forget_last_stored_bs()
    client = _client()

    run(sync_panchang(FakeDB(), client=client))

    assert client.calls.count("https://hamropatro.com/rashifal") == 1
    assert len(client.calls) == 2  # the rashifal page and the day page


def test_the_horoscopes_are_not_rewritten_when_the_day_has_not_rolled_over():
    """
    The same rule as the almanac: the horoscopes do not change
    within a day, so a second pass in the same day does not write
    them again.
    """
    forget_last_stored_bs()
    client = _client()
    db = FakeDB()

    run(sync_panchang(db, client=client))
    first = len(db.upserts)

    run(sync_panchang(db, client=client))

    assert len(db.upserts) == first
    assert last_stored_signs_bs() == "2083-7-5"


def test_the_horoscopes_are_stored_even_when_the_almanac_is_not():
    """
    The two are separate tables with separate guards. A day whose
    panchang could not be read must still have its horoscopes: a
    missing horoscope is a broken category page and the almanac is
    one line in a header.
    """
    forget_last_stored_bs()
    client = RecordingClient(
        {
            "https://hamropatro.com/rashifal": NORMAL_DAY_HTML,
            "https://hamropatro.com/date/2083-7-5": MALFORMED_HTML,
        }
    )
    db = FakeDB()

    run(sync_panchang(db, client=client))

    rashifal_writes = [
        write for write in db.upserts if write[0] == "daily_rashifal"
    ]
    assert len(rashifal_writes) == 1
    assert len(rashifal_writes[0][1]) == 12


def test_a_page_with_no_signs_does_not_stop_the_almanac():
    """
    A page that has lost its horoscopes is a problem with the
    horoscopes. The almanac is the other half of the pass and it
    must still be stored.
    """
    forget_last_stored_bs()
    client = RecordingClient(
        {
            "https://hamropatro.com/rashifal": NO_RASHIFAL_HTML,
            "https://hamropatro.com/date/2083-7-5": _page(detail=MINIMAL_DETAIL),
        }
    )
    db = FakeDB()

    panchang = run(sync_panchang(db, client=client))

    assert panchang is not None
    assert panchang.tithi == "एकादशी"
    assert [w for w in db.upserts if w[0] == "daily_rashifal"] == []


def test_the_pass_does_not_raise_when_the_source_is_down():
    """
    The horoscopes are part of the same pass, and a source that is
    down must cost the worker nothing.
    """
    forget_last_stored_bs()

    class BrokenClient:
        async def get(self, url):
            raise RuntimeError("the host is unreachable")

        async def aclose(self):
            pass

    db = FakeDB()

    assert run(sync_panchang(db, client=BrokenClient())) is None
    assert db.upserts == []


# =============================================================
# HAS THE SOURCE PUBLISHED THIS DAY'S HOROSCOPE YET?
#
# Measured on 2026-10-23: the source's per-day page for tomorrow
# carried exactly today's twelve horoscopes, and so did the page for
# the day after. It publishes a day's horoscope during that day; until
# it does, its per-day page repeats today's.
#
# Storing that copy put today's horoscope under six other dates: the
# same twelve sentences on seven different days, each with a different
# day of the week beside it. The reader was right to call it wrong
# information.
# =============================================================


def _signs(advice: str):
    """The twelve signs, with every advice replaced by one sentence."""
    signs = [
        {**sign, "descNp": advice, "descEn": ""}
        for sign in RASHIFAL_SIGNS
    ]
    return parse_rashifal(_page(signs=signs))


def test_two_days_with_the_same_advice_are_the_same_horoscope():
    assert same_horoscope(_signs("one"), _signs("one")) is True


def test_two_days_with_different_advice_are_not():
    assert same_horoscope(_signs("one"), _signs("two")) is False


def test_an_empty_day_is_never_the_same_as_a_real_one():
    assert same_horoscope([], _signs("one")) is False
    assert same_horoscope(_signs("one"), []) is False

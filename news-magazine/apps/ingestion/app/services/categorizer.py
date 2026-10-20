from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


# Python's \b is Unicode-aware and does treat Devanagari letters as word
# characters, so "\bकर\b" does not match inside "सरकार".  But \b is defined in
# terms of str.isalnum(), and Devanagari's virama and matras (Unicode
# categories Mn/Mc) are not alphanumeric -- so \b opens a boundary in the
# middle of a conjunct: "\bकल\b" matches inside "स्कल", "\bरो\b" inside "प्रो",
# "\bदिन\b" inside "स्दिन".  Word characters are therefore defined here
# explicitly: alphanumerics, underscore, and every combining mark / format
# character, which between them cover Devanagari letters, matras, the virama
# and the ZWNJ/ZWJ joiners that Nepali headlines are full of ("सर्‍यो").
def _is_word_char(char: str) -> bool:
    if not char:
        return False

    if char.isalnum() or char == "_":
        return True

    return unicodedata.category(char) in ("Mn", "Mc", "Me", "Cf")


@lru_cache(maxsize=None)
def _normalized_keyword(keyword: str) -> str:
    return Categorizer._normalize(keyword)


class Categorizer:
    """
    Lightweight deterministic article categorizer.

    Categories are selected using headline + excerpt keyword matching.
    If no category confidently matches, None is returned so the ingestion
    pipeline can fall back to the source's default category.

    Keywords must match on a WORD BOUNDARY, never inside a longer word.
    Devanagari does not put spaces inside a word, so the old raw
    ``keyword in text`` test made "कर" ("to do") fire on "सरकार",
    "कार्यालय", "प्रकार" -- which is why /category/business was full of
    disaster and health stories.  See ``_matches``.
    """

    RULES: dict[str, tuple[str, ...]] = {
        "politics": (
            "राजनीति",
            "सरकार",
            "मन्त्री",
            "मंत्रि",
            "प्रधानमन्त्री",
            "सांसद",
            "संसद",
            "निर्वाचन",
            "चुनाव",
            "दल",
            "नेपाली कांग्रेस",
            "एमाले",
            "माओवादी",
            "president",
            "prime minister",
            "minister",
            "government",
            "parliament",
            "election",
            "politics",
        ),
        "business": (
            "अर्थतन्त्र",
            "अर्थ",
            "व्यापार",
            "बैंक",
            "बैंकिङ",
            "शेयर",
            "सेयर",
            "बजार",
            "लगानी",
            "राजस्व",
            # "कर" (tax) was REMOVED. It is the ordinary verb "to do" and it
            # is the opening syllable of a huge share of Nepali vocabulary, so
            # any headline carrying सरकार, कार्यालय, प्रकार, निर्माणकर्ता,
            # प्रकरण, ध्रुवीकरण, आकर्षण or ठक्कर scored business. On a sample
            # of real production headlines every single "कर" match was a
            # false positive. "राजस्व" already covers tax revenue.
            "उद्योग",
            "व्यवसाय",
            "business",
            "economy",
            "bank",
            "banking",
            "stock",
            "market",
            "investment",
            "finance",
        ),
        "technology": (
            "प्रविधि",
            "टेक्नोलोजी",
            "एआई",
            "कृत्रिम बुद्धिमत्ता",
            "सफ्टवेयर",
            "मोबाइल",
            "इन्टरनेट",
            "डिजिटल",
            "technology",
            "tech",
            "artificial intelligence",
            "software",
            "mobile",
            "internet",
            "digital",
        ),
        "sports": (
            "खेल",
            "क्रिकेट",
            "फुटबल",
            "विश्वकप",
            "खेलाडी",
            "टिम",
            "टोली",
            "cricket",
            "football",
            "sports",
            "player",
            "team",
            "world cup",
        ),
        "entertainment": (
            "मनोरञ्जन",
            "चलचित्र",
            "फिल्म",
            "सिनेमा",
            "अभिनेता",
            "अभिनेत्री",
            "गायक",
            "गीत",
            "संगीत",
            "entertainment",
            "movie",
            "film",
            "cinema",
            "actor",
            "actress",
            "music",
        ),
        "health": (
            "स्वास्थ्य",
            "अस्पताल",
            "रोग",
            "औषधि",
            "चिकित्सा",
            "डाक्टर",
            "स्वास्थ्यकर्मी",
            "health",
            "hospital",
            "disease",
            "medicine",
            "medical",
            "doctor",
        ),
        "world": (
            "अमेरिका",
            "भारत",
            "चीन",
            "बेलायत",
            "रुस",
            "अन्तर्राष्ट्रिय",
            "विश्व",
            "international",
            "world",
            "america",
            "india",
            "china",
            "uk",
            "russia",
        ),
    }

    # Keywords that must match as a WHOLE word rather than as the opening of
    # one. Every other keyword is allowed to continue into a suffix, because
    # Nepali glues case endings straight onto the stem with no space (खेल ->
    # खेलमा, बजार -> बजारलाई); requiring a trailing boundary too would throw
    # most of the corpus away. These are the exceptions -- English short forms
    # whose stem is not a stem. "ai" is the dangerous one: on word-start alone
    # it fires on "air", "airport", "airstrike", "aids" and "ailment", and as
    # a raw substring it fired on "said" and "rain".
    EXACT_KEYWORDS: frozenset[str] = frozenset({"ai"})

    # Tie-break order, written out rather than inherited from the order RULES
    # happens to be declared in. Desks that own the more specific word come
    # first, so a headline that trips two categories equally lands on the right
    # one: "विश्व" is the opening of "विश्वकप", so a World Cup story scores
    # world and sports equally and must resolve to sports; a Bollywood film
    # shot in India scores entertainment and world equally and must resolve to
    # entertainment.
    PRIORITY: tuple[str, ...] = (
        "politics",
        "business",
        "technology",
        "sports",
        "entertainment",
        "health",
        "world",
    )

    # A headline keyword is worth HEADLINE_WEIGHT, an excerpt keyword
    # EXCERPT_WEIGHT. Nothing weaker than MIN_SCORE is filed at all: a single
    # excerpt hit scores 1, so an article needs either one keyword in the
    # headline or two in the excerpt before it is put in a specific category.
    # Below that, classify() returns None and the pipeline falls back to
    # national -- far better than guessing a specific desk and being wrong.
    HEADLINE_WEIGHT = 3
    EXCERPT_WEIGHT = 1
    MIN_SCORE = 2

    def __init__(self, slug_to_id: dict[str, str]):
        self.slug_to_id = slug_to_id

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""

        value = value.lower()
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    @staticmethod
    def _matches(text: str, keyword: str) -> bool:
        """
        True when `keyword` occurs in `text` on a word boundary.

        The keyword must not start in the middle of a word -- that is what
        keeps "कर" out of "सरकार". It MAY be followed by more of the same
        word, because Devanagari case endings attach directly to the stem:
        "बजार" must still match inside "बजारलाई", and "खेल" inside "खेलमै".
        Keywords in EXACT_KEYWORDS must also end on a boundary.
        """
        exact = keyword in Categorizer.EXACT_KEYWORDS
        start = text.find(keyword)

        while start != -1:
            end = start + len(keyword)
            before = text[start - 1] if start else ""
            after = text[end] if end < len(text) else ""

            if not _is_word_char(before) and (
                not exact or not _is_word_char(after)
            ):
                return True

            start = text.find(keyword, start + 1)

        return False

    def _priority_of(self, slug: str) -> int:
        try:
            return self.PRIORITY.index(slug)
        except ValueError:
            # Not a named desk: every unlisted category ranks after every
            # listed one, and ties among them fall through to the slug.
            return len(self.PRIORITY)

    def classify(
        self,
        headline: str | None,
        excerpt: str | None = None,
    ) -> str | None:
        """
        Return the matching category UUID.

        Headline matches receive more weight than excerpt matches.
        """

        title = self._normalize(headline)
        summary = self._normalize(excerpt)

        if not title and not summary:
            return None

        scores: dict[str, int] = {}
        headline_scores: dict[str, int] = {}

        for slug, keywords in self.RULES.items():
            score = 0
            headline_hits = 0

            for raw_keyword in keywords:
                keyword = _normalized_keyword(raw_keyword)

                if not keyword:
                    continue

                # Stronger signal from headline.
                if self._matches(title, keyword):
                    headline_hits += 1
                    score += self.HEADLINE_WEIGHT

                # Supporting signal from excerpt.
                if self._matches(summary, keyword):
                    score += self.EXCERPT_WEIGHT

            if score:
                scores[slug] = score
                headline_scores[slug] = headline_hits

        if not scores:
            return None

        # Total order, so the winner never depends on dict iteration order:
        # strongest score, then the category whose keyword was in the HEADLINE,
        # then the declared desk priority, then the slug itself.
        best_slug = min(
            scores,
            key=lambda slug: (
                -scores[slug],
                -headline_scores[slug],
                self._priority_of(slug),
                slug,
            ),
        )

        if scores[best_slug] < self.MIN_SCORE:
            return None

        # Only return categories that actually exist
        # in the database.
        return self.slug_to_id.get(best_slug)

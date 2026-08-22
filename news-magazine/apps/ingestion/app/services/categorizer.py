from __future__ import annotations

import re


class Categorizer:
    """
    Lightweight deterministic article categorizer.

    Categories are selected using headline + excerpt keyword matching.
    If no category confidently matches, None is returned so the ingestion
    pipeline can fall back to the source's default category.
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
            "कर",
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
            "ai",
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

    def __init__(self, slug_to_id: dict[str, str]):
        self.slug_to_id = slug_to_id

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""

        value = value.lower()
        value = re.sub(r"\s+", " ", value)
        return value.strip()

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

        for slug, keywords in self.RULES.items():
            score = 0

            for keyword in keywords:
                keyword = self._normalize(keyword)

                if not keyword:
                    continue

                # Stronger signal from headline.
                if keyword in title:
                    score += 3

                # Supporting signal from excerpt.
                if keyword in summary:
                    score += 1

            if score:
                scores[slug] = score

        if not scores:
            return None

        best_slug = max(
            scores,
            key=scores.get,
        )

        # Only return categories that actually exist
        # in the database.
        return self.slug_to_id.get(best_slug)

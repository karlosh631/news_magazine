"""
Simple keyword-based category classifier. This is intentionally a stub:
it gets ingestion working end-to-end without an ML dependency, and is the
seam where AI-assisted classification (section 41 of the spec) plugs in
later — swap `Categorizer.classify` for a model call without touching the
rest of the pipeline. Any AI-suggested category should still be marked
and routed through the editorial workflow, never auto-published.
"""
from __future__ import annotations

KEYWORD_MAP: dict[str, list[str]] = {
    "politics": ["parliament", "election", "minister", "प्रधानमन्त्री", "चुनाव", "संसद"],
    "business": ["market", "economy", "bank", "stock", "व्यापार", "अर्थतन्त्र"],
    "sports": ["cricket", "football", "match", "tournament", "क्रिकेट", "खेल"],
    "technology": ["technology", "app", "software", "ai", "प्रविधि"],
    "entertainment": ["movie", "film", "actor", "मनोरञ्जन", "चलचित्र"],
    "health": ["hospital", "health", "covid", "स्वास्थ्य"],
    "education": ["school", "university", "exam", "शिक्षा"],
}


class Categorizer:
    def __init__(self, category_slug_to_id: dict[str, str]):
        self.category_slug_to_id = category_slug_to_id

    def classify(self, headline: str, excerpt: str | None) -> str | None:
        text = f"{headline} {excerpt or ''}".lower()
        for slug, keywords in KEYWORD_MAP.items():
            if slug not in self.category_slug_to_id:
                continue
            if any(kw.lower() in text for kw in keywords):
                return self.category_slug_to_id[slug]
        return None

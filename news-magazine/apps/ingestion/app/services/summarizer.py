"""
Four-point extractive summariser.

Every card on the homepage shows the story reduced to exactly four
points, and every one of those points is a verbatim sentence from the
article the outlet actually published. Nothing here generates text.

This is deliberately an EXTRACTIVE summariser, not a generative one.
An LLM asked to "summarise this news" will happily invent a plausible
sounding fact that no outlet reported, and a news portal that shows
invented facts under a real outlet's name is worse than one that shows
nothing. Gemini is available as an optional *polisher* (see
`polish_with_gemini`) but it is off by default and never invents
content — it only selects which of the extracted sentences to lead
with, and falls back to the extractive result on any error.

The scoring is Term Frequency-Inverse Document Frequency over the
article's own sentences, with position and length shaping. That is
enough for single-news-article summarisation, which is a much easier
problem than summarising a corpus.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass

from app.services.sanitizer import sanitize_plain_text

logger = logging.getLogger("summarizer")


# =============================================================
# TUNING
# =============================================================

# The user-facing contract: four points per story.
NUM_POINTS = 4

MIN_SENTENCE_CHARS = 40
MAX_SENTENCE_CHARS = 320

# Sentences need enough substance to be a point on its own.
MIN_SENTENCE_WORDS = 7

# Sentences this short carry no information ("Nepal on Monday.").
BOILERPLATE_RE = re.compile(
    r"^(?:read more|also read|related|advertisement|click here|follow us|"
    r"share this|photograph|photo:|image:|source:|updated|published|"
    r"by [a-z]+ [a-z]+$|subscribe|sign up)",
    re.IGNORECASE,
)

# Junk that appears in nearly every Nepali news article and therefore
# dominates TF-IDF if left in.
_STOPWORDS = frozenset(
    """
    a an the and or but of in on at to for from by as is are was were be been being
    this that these those it its his her their our your my he she they we you i
    said says say told according also after before during while when where which who
    whose what how why more most less least very much many few some any all both
    each other than then there here not no nor only own same so such can could will
    would shall should may might must do does did done have has had having
    news new latest update updates reported report breaking live
    """.split()
)

_NEPALI_STOPWORDS = frozenset(
    """
    छ छन् छैन हो होइन भए भन्दा गर्न गरेको गर्ने लागि बाट मा नै यो त्यो उनी उहाँ
    हामी तपाईं तर तापनि कि किन भने पनि अझै आज हिजो भोलि सबै धेरै कम एक दुई
    अनि फेरि जब जुन त्यसैले हुन सक्छ भएको भएका
    """.split()
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[।!?।।]|[.!?])\s+(?=[A-Zऀ-ॿ\"'(])")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Boilerplate that lives in the page chrome rather than the story.
_CHROME_RE = re.compile(
    r"<(nav|header|footer|aside|form|script|style|noscript|figure figcaption|"
    r"div class=\"[^\"]*(?:share|comment|related|newsletter|advert|promo|"
    r"social|subscribe|breadcrumb|sidebar|widget))[>]",
    re.IGNORECASE,
)


@dataclass
class Summary:
    """A four-point extractive summary of one article."""

    points: list[str]
    source_sentences: int = 0
    method: str = "extractive"

    def __bool__(self) -> bool:
        return bool(self.points)

    def as_dict(self) -> dict:
        return {
            "points": self.points,
            "source_sentences": self.source_sentences,
            "method": self.method,
        }


# =============================================================
# TEXT PREPARATION
# =============================================================

def html_to_text(html: str) -> str:
    """Flatten article HTML to plain text, dropping page chrome."""
    if not html:
        return ""

    # Remove script/style blocks wholesale first — they contain prose-like
    # strings that would otherwise be mined for "sentences".
    text = re.sub(
        r"<(script|style|noscript)\b[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)

    # Prefer the <article> block when the page marks one up.
    article_block = re.search(
        r"<article\b[^>]*>(.*?)</article>", text, re.IGNORECASE | re.DOTALL
    )
    if article_block and len(article_block.group(1)) > 500:
        text = article_block.group(1)

    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def split_sentences(text: str) -> list[str]:
    """
    Split into sentences, handling both Latin (.) and Devanagari (।) marks.

    Nepali articles frequently interleave scripts mid-sentence, so both
    terminators are accepted and a sentence must also clear a length bar
    before it is considered usable.

    HTML is flattened first. Without that, a paragraph boundary like
    `...</p>\n\n<p>A Tata...` never matches the split pattern (the char
    after the full stop is `<`, not whitespace), so an entire HTML
    article came back as one over-long part and yielded zero sentences.
    """
    if not text:
        return []

    parts = _SENTENCE_SPLIT_RE.split(html_to_text(text))
    sentences: list[str] = []

    for part in parts:
        cleaned = sanitize_plain_text(part).strip()
        if not cleaned:
            continue
        if BOILERPLATE_RE.match(cleaned):
            continue
        if len(cleaned) < MIN_SENTENCE_CHARS or len(cleaned) > MAX_SENTENCE_CHARS:
            continue
        if len(cleaned.split()) < MIN_SENTENCE_WORDS:
            continue
        sentences.append(cleaned)

    return sentences


def _tokens(text: str) -> list[str]:
    """Content words, stopwords removed. Keeps Devanagari runs intact."""
    words = re.findall(r"[ऀ-ॿa-zA-Z0-9']+", text.lower())
    return [
        word.strip("'")
        for word in words
        if word not in _STOPWORDS
        and word not in _NEPALI_STOPWORDS
        and len(word) > 1
    ]


# =============================================================
# SCORING
# =============================================================

def score_sentences(sentences: list[str]) -> list[float]:
    """
    TF-IDF sentence score with position and length shaping.

    TF-IDF is computed against the article itself (each sentence is a
    "document"), which is what makes it pick out the sentences carrying
    the article's distinctive vocabulary rather than its boilerplate.
    """
    if not sentences:
        return []

    tokenized = [_tokens(sentence) for sentence in sentences]

    # Document frequency across the article's own sentences.
    doc_freq: Counter[str] = Counter()
    for tokens in tokenized:
        doc_freq.update(set(tokens))

    total_docs = len(sentences)
    scores: list[float] = []

    for index, (sentence, tokens) in enumerate(zip(sentences, tokenized)):
        if not tokens:
            scores.append(0.0)
            continue

        # Summed TF-IDF, length-normalised so a long sentence does not
        # win purely by having more words.
        tfidf_sum = 0.0
        for token in tokens:
            idf = math.log(total_docs / (1 + doc_freq[token])) + 1.0
            tfidf_sum += idf

        length_norm = tfidf_sum / math.sqrt(len(tokens))

        # News puts the lede first; a decaying position prior captures
        # that without hard-coding "always pick sentence 0".
        position_prior = 1.0 / (1.0 + 0.35 * index)

        # Mid-length sentences tend to be complete points.
        word_count = len(tokens)
        length_prior = 1.0
        if word_count < 10:
            length_prior = 0.75
        elif word_count > 60:
            length_prior = 0.85

        scores.append(length_norm * position_prior * length_prior)

    return scores


def _dedupe_points(points: list[str], threshold: float = 90.0) -> list[str]:
    """
    Drop points that say the same thing as one already chosen.

    Threshold is deliberately very high (90 on fuzz.ratio). News
    sentences about the same topic share most of their vocabulary, and
    a topical-similarity threshold collapses them: four sentences about
    one temple festival scored 72-76 against each other while saying
    four different things, so a 0.72 threshold reduced a 35-sentence
    article to a single point.

    Only near-verbatim repeats are dropped here — the lede restating
    the subhead is the actual duplicate case worth catching.
    """
    from app.services.dedup import similarity

    kept: list[str] = []
    for candidate in points:
        if all(similarity(candidate, existing) < threshold for existing in kept):
            kept.append(candidate)

    return kept


def _shorten(point: str, max_chars: int = 220) -> str:
    """Trim a point at a sentence or clause boundary, never mid-word."""
    if len(point) <= max_chars:
        return point

    truncated = point[:max_chars].rsplit(" ", 1)[0].rstrip(",;:")
    return truncated + "…"


# Leading breadcrumb/section words that extractors pull in with the first
# sentence. Kathmandu Post yields "National Three women killed..." because
# the section label precedes the story body in the same text node.
_BREADCRUMB_PREFIX_RE = re.compile(
    r"^(?:national|politics|business|technology|sports|entertainment|world|"
    r"health|education|money|opinion|art-culture|columns|visual-stories|"
    r"province-[a-z0-9-]+|राष्ट्रिय|राजनीति|व्यापार|प्रविधि|खेलकुद|विश्व)\s+",
    re.IGNORECASE,
)


def _strip_breadcrumb(point: str, headline: str | None = None) -> str:
    """
    Remove a section label and any repeated headline from the front.

    Extraction frequently starts the body with "National <headline>. The
    actual lede...", which reads as noise on a card. Trim the section
    word, then drop a leading copy of the headline if it is present, but
    only when real sentence content survives afterwards.
    """
    cleaned = _BREADCRUMB_PREFIX_RE.sub("", point).strip()

    if headline:
        normalized_headline = re.sub(r"\s+", " ", headline).strip()
        # Only drop it if it genuinely starts the point.
        if cleaned.lower().startswith(normalized_headline.lower()[:40]) and len(normalized_headline) > 25:
            remainder = cleaned[len(normalized_headline):].lstrip(" .:—-")
            if len(remainder) > 60:
                cleaned = remainder

    return cleaned or point


# =============================================================
# PUBLIC API
# =============================================================

def summarize_article(
    text: str | None,
    headline: str | None = None,
    num_points: int = NUM_POINTS,
) -> Summary:
    """
    Reduce one article to `num_points` real sentences.

    Args:
        text: Plain article text (or HTML — it is flattened either way).
        headline: Used to bias selection toward sentences that carry the
                  headline's distinctive terms, since those are the
                  points a reader scanning the card expects.
        num_points: How many points to return. Defaults to 4.

    Returns a Summary whose `points` are verbatim sentences from the
    article. Returns an empty Summary when the article is too short to
    summarise — the caller then falls back to the excerpt.
    """
    flat = html_to_text(text or "")
    sentences = split_sentences(flat)

    if not sentences:
        return Summary(points=[], source_sentences=0)

    scores = score_sentences(sentences)

    # Boost sentences that share distinctive vocabulary with the
    # headline. Weight is deliberately modest so a headline-matching
    # but contentless sentence cannot outrank real substance.
    if headline:
        headline_tokens = set(_tokens(headline))
        if headline_tokens:
            boosted = []
            for sentence, score in zip(sentences, scores):
                overlap = len(set(_tokens(sentence)) & headline_tokens)
                boosted.append(score * (1.0 + 0.15 * min(overlap, 4)))
            scores = boosted

    ranked = sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)

    # Over-select generously, then re-sort by original position so the
    # points read in the article's own order. Over-selecting matters:
    # dedup will remove repeats, and a short article may not have
    # num_points distinct high-scoring sentences.
    chosen = ranked[: max(num_points * 6, 24)]
    chosen.sort()

    points = _dedupe_points([sentences[index] for index in chosen])
    points = [
        _shorten(_strip_breadcrumb(point, headline))
        for point in points[:num_points]
    ]

    return Summary(points=points, source_sentences=len(sentences))


def summarize_from_html(
    html: str | None,
    headline: str | None = None,
    fallback_excerpt: str | None = None,
    num_points: int = NUM_POINTS,
) -> Summary:
    """
    Summarise an article page, degrading gracefully.

    Order of preference:
      1. Four points from the full article body.
      2. Points from the excerpt, when the body was not retrievable
         (paywalled, JS-rendered, or robots-disallowed).
      3. The excerpt itself, split on sentence boundaries.
      4. Empty — the caller renders the raw excerpt instead.

    Never invents text at any level.
    """
    summary = summarize_article(html, headline=headline, num_points=num_points)
    if summary.points:
        return summary

    if fallback_excerpt:
        from_summary = summarize_article(
            fallback_excerpt, headline=headline, num_points=num_points
        )
        if from_summary.points:
            from_summary.method = "extractive_from_excerpt"
            return from_summary

        cleaned = sanitize_plain_text(fallback_excerpt)
        if cleaned:
            parts = split_sentences(cleaned) or [cleaned]
            return Summary(
                points=[_shorten(part) for part in parts[:num_points]],
                source_sentences=len(parts),
                method="excerpt_fallback",
            )

    return Summary(points=[])


async def polish_with_gemini(
    points: list[str],
    headline: str,
    enabled: bool = False,
) -> list[str]:
    """
    Optional: pick which extracted points to lead with.

    Off by default. Even when enabled the model is only allowed to
    REORDER and SELECT from the sentences it was given — it cannot
    introduce new text, because anything not present in `points` is
    discarded before returning. On any failure the extractive order is
    returned unchanged.
    """
    if not enabled or not points:
        return points

    import os

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("Gemini_API_Key")
    if not api_key:
        return points

    import httpx

    numbered = "\n".join(f"[{i + 1}] {point}" for i, point in enumerate(points))
    prompt = (
        "You are ranking existing news summary points by importance.\n"
        f"Headline: {headline}\n\nPoints:\n{numbered}\n\n"
        "Return ONLY a JSON array of the point numbers in the order a reader "
        "should see them, most important first. Do not write, edit or add text. "
        "Return every number exactly once."
    )

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-flash-latest:generateContent",
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "maxOutputTokens": 100,
                        "responseMimeType": "application/json",
                    },
                },
            )
            response.raise_for_status()
            raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            import json

            order = json.loads(raw.strip().replace("```json", "").replace("```", ""))

        if not isinstance(order, list):
            return points

        reordered: list[str] = []
        for position in order:
            index = int(position) - 1
            # Discard anything that is not a verbatim member of `points`.
            if 0 <= index < len(points) and points[index] not in reordered:
                reordered.append(points[index])

        for index, point in enumerate(points):
            if point not in reordered:
                reordered.append(point)

        return reordered[: len(points)]

    except Exception:
        # Never let optional polish change what the reader sees.
        logger.debug("Summary polish skipped for %r", headline[:60])
        return points
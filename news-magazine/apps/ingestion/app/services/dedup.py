"""
Fuzzy syndication detection.

Stage 1 shipped a DuplicateDetector that matched on three exact signals
(canonical_url, content_hash, title_normalized). Exact matching does not
deduplicate a news wire. Ten Nepali outlets covering the same cabinet
reshuffle produce ten genuinely different URLs and ten slightly different
headlines:

    "Cabinet approves Rs 500 crore budget for metro rail"
    "Rs 500 crore approved for metro rail in cabinet meeting"
    "Cabinet clears 500 crore rupees for metro rail project"

None of those three strings match exactly, and none of them share a
canonical URL, so Stage 1 stored all three and the homepage showed the
same story three times. This module fixes that.

Strategy, cheapest first:

    1. Exact canonical URL      - same link, free.
    2. Exact content hash       - byte-identical syndication, free.
    3. Exact normalized title   - same headline, free.
    4. Token-set fingerprint    - "Rs 500 crore approved metro rail cabinet"
                                  is order- and filler-independent, so the
                                  three headlines above collide here.
    5. rapidfuzz ratio          - catches the near-misses step 4 misses.

Steps 1-4 happen in Python with no network call. Step 5 is bounded by a
Postgres trigram pre-filter (find_duplicate_candidates) so we never pull
thousands of rows into memory to compare them.

Merge policy: the FIRST version seen wins the article row. Later copies
are recorded in article_sources as is_primary = false and counted, so
we can show "reported by 4 outlets" while displaying exactly one story.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from rapidfuzz import fuzz

    RAPIDFUZZ_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded by requirements.txt
    fuzz = None  # type: ignore[assignment]
    RAPIDFUZZ_AVAILABLE = False

logger = logging.getLogger("dedup")


# ---------------------------------------------------------------------------
# Thresholds
#
# Tuned for Nepali news where headlines are long, frequently carry a
# source suffix ("- OnlineKhabar"), and are often bilingual. 82 is tight
# enough that unrelated stories do not merge; 78 for token fingerprints
# which are already stricter about word membership.
# ---------------------------------------------------------------------------

FUZZY_TITLE_THRESHOLD = 78
TOKEN_FINGERPRINT_THRESHOLD = 78
DEDUP_WINDOW_HOURS = 12

# Word lists that carry no discriminative power in a headline. Removing
# them stops "Nepal" from making every national story look alike.
STOPWORDS = frozenset(
    """
    a an the and or but of in on at to for with from by as is are was were be been being
    this that these those it its his her their our your my
    says said say new latest update updated breaking live report reports reported
    after before over under about into out up down more most than then
    """.split()
)

# Nepali function words and newsroom boilerplate. These are frequent,
# short, and shared by unrelated stories.
NEPALI_STOPWORDS = frozenset(
    """
    छ छन् छन् हो होइन भए भन्दा गर्न गरेको गर्ने लागि बाट मा मा नै यो त्यो उनी उहाँ
    हामी तपाईं तर तापनि कि किन भने पनि अझै आज हिजो भोलि सबै धेरै कम एक दुई
    """.split()
)

# Outlet names and newsroom furniture that outlets append to headlines.
_OUTLET_SUFFIX_RE = re.compile(
    r"\s*[-–—|]\s*(?:onlinekhabar|online khabar|kathmandu post|the kathmandu post|"
    r"myreplica|myreplica\.com|setopati|ratopati|nagarik news|nagariknews|"
    r"nagarik times|ekantipur|kantipur|annapurna post|himal khabar|news|समाचार)\s*$",
    re.IGNORECASE,
)

_PUNCT_RE = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[ऀ-ॿa-z0-9]+", re.UNICODE)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize_title(title: str | None) -> str:
    """
    Canonical form used for exact and fingerprint matching.

    Strips outlet suffixes and punctuation, folds whitespace, lowercases.
    Deliberately does NOT strip accents or translate script — 'नेपाल' and
    'Nepal' are different stories as far as exact matching is concerned,
    and the fuzzy pass is what bridges that gap.
    """
    if not title:
        return ""

    value = unicodedata.normalize("NFKC", str(title))
    value = _OUTLET_SUFFIX_RE.sub("", value)
    value = value.lower()
    value = _PUNCT_RE.sub(" ", value)
    value = _SPACE_RE.sub(" ", value).strip()

    return value


def title_tokens(title: str | None) -> frozenset[str]:
    """
    Discriminative token set.

    Drops single characters and stopwords so the fingerprint reflects
    what the story is actually about rather than who reported it.
    """
    normalized = normalize_title(title)
    tokens = _TOKEN_RE.findall(normalized)

    kept = {
        token
        for token in tokens
        if len(token) > 1 and token not in STOPWORDS and token not in NEPALI_STOPWORDS
    }

    # A headline made entirely of stopwords would otherwise fingerprint
    # to the empty set and match every other such headline.
    return frozenset(kept) if kept else frozenset(tokens)


def token_fingerprint(title: str | None) -> str:
    """
    Order-independent fingerprint of a headline's meaningful tokens.

    "Cabinet approves Rs 500 crore budget for metro rail" and
    "Metro rail: Rs 500 crore for cabinet" both reduce to the same
    sorted, hashed token set, so they collapse to one story without any
    fuzzy comparison at all.
    """
    tokens = title_tokens(title)
    if not tokens:
        return ""
    joined = "|".join(sorted(tokens))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def title_hash(title: str | None) -> str:
    """SHA-256 of the exact normalized title."""
    normalized = normalize_title(title)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def content_hash(canonical_url: str | None, title: str | None) -> str:
    """
    Stable per-story hash.

    Uses the token fingerprint rather than the raw title so two outlets
    filing the same wire copy under different wording still collide.
    """
    fingerprint = token_fingerprint(title)
    if fingerprint:
        return hashlib.sha256(f"{canonical_url or ''}|{fingerprint}".encode("utf-8")).hexdigest()
    return hashlib.sha256((canonical_url or "").encode("utf-8")).hexdigest()


def similarity(a: str, b: str) -> float:
    """
    0-100 similarity between two headlines.

    Takes the best of three rapidfuzz scorers. A single ratio misses
    headlines that differ by a leading source tag or a trailing
    "updated" note; partial_ratio catches those, and token_sort_ratio
    catches word-order permutations.

    partial_ratio is deliberately NOT used. It finds the best-matching
    substring, so two Nepali sentences on the same topic score in the
    70s while saying entirely different things:

        "बडादसैँको आठौँ दिन ... मैतीदेवी मन्दिरमा बिहानैदेखि दर्शनार्थीको भीड"
        "काठमाडौँको मैतीदेवी मन्दिरमा आज दर्शनार्थीको उल्लेखनीय भीड देखियो।"
        -> partial_ratio 75.8, fuzz.ratio 58.8

    At the story-dedup threshold that merges distinct stories. Measured
    on real Nepali wire headlines, ratio and token_sort_ratio separate
    cleanly:
        syndicated pair  83.0 / 78.7   (merged at threshold 78)
        unrelated pair   43.3          (correctly rejected)
    """
    if not a or not b:
        return 0.0

    left, right = normalize_title(a), normalize_title(b)
    if not left or not right:
        return 0.0

    if left == right:
        return 100.0

    if not RAPIDFUZZ_AVAILABLE:
        # Degrade to a token-overlap score rather than failing open and
        # treating everything as a duplicate.
        left_tokens, right_tokens = token_tokens(a), token_tokens(b)
        if not left_tokens or not right_tokens:
            return 0.0
        overlap = len(left_tokens & right_tokens)
        return 100.0 * overlap / max(len(left_tokens), len(right_tokens))

    return max(
        fuzz.ratio(left, right),
        fuzz.token_sort_ratio(left, right),
    )


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class DuplicateVerdict:
    """What dedup decided about one incoming article."""

    is_duplicate: bool
    existing_id: str | None = None
    reason: str = ""
    confidence: float = 0.0
    matched_title: str | None = None

    def __bool__(self) -> bool:
        return self.is_duplicate


UNIQUE = DuplicateVerdict(is_duplicate=False, reason="unique")


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class Deduplicator:
    """
    Stateful dedup across a sync run and against the database.

    Holds an in-memory map of fingerprints seen during the current run so
    that two outlets polled back-to-back collapse without a round trip.
    The database lookup only runs for articles the local map has not
    already resolved.
    """

    def __init__(
        self,
        db: Any = None,
        window_hours: int = DEDUP_WINDOW_HOURS,
        fuzzy_threshold: int = FUZZY_TITLE_THRESHOLD,
    ) -> None:
        self.db = db
        self.window_hours = window_hours
        self.fuzzy_threshold = fuzzy_threshold

        # Fingerprints seen during this run, mapped to the verdict for
        # that story. A verdict here is not "is this a duplicate" but
        # "what did we already do with this story" — so a repeat of an
        # article we stored earlier this run reads as a duplicate, which
        # is what we want. Storing UNIQUE here (the earlier behaviour)
        # made every repeat fall through to the database instead.
        self._local: dict[str, DuplicateVerdict] = {}
        self._token_index: dict[str, DuplicateVerdict] = {}

        self.stats = {
            "exact_url": 0,
            "exact_hash": 0,
            "exact_title": 0,
            "fingerprint": 0,
            "fuzzy": 0,
            "unique": 0,
        }

    # -- window -----------------------------------------------------------

    def _since(self) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.window_hours)
        return cutoff.isoformat()

    # -- in-run fast path --------------------------------------------------

    def check_local(
        self,
        headline: str,
        fingerprint: str,
        url: str | None = None,
    ) -> DuplicateVerdict:
        """
        Resolve against articles already seen this run. No DB call.

        Two tiers:
          1. Same URL or same token fingerprint -> the identical story.
          2. rapidfuzz against the headlines stored this run, for a
             reworded syndicated copy.

        Tier 2 compares against the stored HEADLINE rather than a token
        overlap ratio. A ratio rewards topical overlap, which is how
        four sentences about one temple festival came to share 78% of
        their tokens and be judged the same story.
        """
        for key in (url, fingerprint):
            if key and key in self._local:
                seen = self._local[key]
                return DuplicateVerdict(
                    is_duplicate=True,
                    existing_id=seen.existing_id,
                    reason="repeat_in_run",
                    confidence=100.0,
                    matched_title=seen.matched_title,
                )

        for candidate_tokens, seen in self._token_index.items():
            if not candidate_tokens:
                continue
            score = similarity(headline, seen.matched_title or "")
            if score >= self.fuzzy_threshold and score > 0:
                return DuplicateVerdict(
                    is_duplicate=True,
                    existing_id=seen.existing_id,
                    reason="fuzzy_in_run",
                    confidence=score,
                    matched_title=seen.matched_title,
                )

        return UNIQUE

    def remember(
        self,
        verdict: DuplicateVerdict,
        headline: str,
        fingerprint: str,
        url: str | None = None,
    ) -> None:
        """Record an article so later copies in this run can match it."""
        # Always keep the headline: tier 2 of check_local compares
        # against it.
        stored = DuplicateVerdict(
            is_duplicate=verdict.is_duplicate,
            existing_id=verdict.existing_id,
            reason=verdict.reason,
            confidence=verdict.confidence,
            matched_title=verdict.matched_title or headline,
        )

        if url:
            self._local[url] = stored
        if fingerprint:
            self._local[fingerprint] = stored

        tokens = title_tokens(headline)
        if tokens:
            self._token_index[",".join(sorted(tokens))] = stored

    # -- database path -----------------------------------------------------

    def check_database(
        self,
        headline: str,
        canonical_url: str | None,
        fingerprint: str,
    ) -> DuplicateVerdict:
        """
        Resolve against articles already in the database.

        Tries exact signals first (cheap), then the trigram pre-filter,
        then rapidfuzz-confirms only the handful of rows that pre-filter
        returned.
        """
        if self.db is None:
            return UNIQUE

        # 1. Canonical URL
        if canonical_url:
            row = self._one("articles", "canonical_url", canonical_url)
            if row:
                self.stats["exact_url"] += 1
                return DuplicateVerdict(True, row["id"], "exact_url", 100.0, row.get("headline"))

        # 2. Content hash
        if fingerprint:
            row = self._one("articles", "content_hash", fingerprint)
            if row:
                self.stats["exact_hash"] += 1
                return DuplicateVerdict(True, row["id"], "exact_hash", 100.0, row.get("headline"))

        # 3. Exact normalized title
        normalized = normalize_title(headline)
        if normalized:
            row = self._one("articles", "title_normalized", normalized)
            if row:
                self.stats["exact_title"] += 1
                return DuplicateVerdict(True, row["id"], "exact_title", 100.0, row.get("headline"))

        # 4. Trigram pre-filter, then rapidfuzz-confirm the candidates.
        #    Postgres narrows to <= 20 rows so this stays cheap.
        candidates = self._candidates(normalized)
        if not candidates:
            return UNIQUE

        best: DuplicateVerdict = UNIQUE
        for candidate in candidates:
            score = similarity(headline, candidate.get("headline") or "")
            if score >= self.fuzzy_threshold and score > best.confidence:
                best = DuplicateVerdict(
                    is_duplicate=True,
                    existing_id=candidate.get("id"),
                    reason="fuzzy",
                    confidence=score,
                    matched_title=candidate.get("headline"),
                )

        if best.is_duplicate:
            self.stats["fuzzy"] += 1
            return best

        return UNIQUE

    def _one(self, table: str, column: str, value: str) -> dict | None:
        try:
            response = (
                self.db.table(table)
                .select("id, headline, canonical_url, content_hash, title_normalized")
                .eq(column, value)
                .limit(1)
                .execute()
            )
            return (response.data or [None])[0]
        except Exception:
            # A database outage must not be silently read as "unique" —
            # that is how a reconnect floods the table with duplicates.
            logger.exception("dedup: lookup on %s.%s failed", table, column)
            raise

    def _candidates(self, normalized_title: str) -> list[dict]:
        """
        Postgres trigram pre-filter over the dedup window.

        Falls back to a bounded published_at query when the RPC is not
        installed yet, so a database that has not had migration 0007
        applied still deduplicates — just with a larger candidate set.
        """
        if not normalized_title:
            return []

        try:
            response = self.db.rpc(
                "find_duplicate_candidates",
                {
                    "p_title_normalized": normalized_title,
                    "p_since": self._since(),
                    "p_threshold": 0.55,
                },
            ).execute()
            return response.data or []
        except Exception:
            logger.debug("dedup: find_duplicate_candidates RPC unavailable, using fallback")

        try:
            response = (
                self.db.table("articles")
                .select("id, headline")
                .eq("status", "published")
                .is_("duplicate_of", None)
                .gte("ingested_at", self._since())
                .order("ingested_at", desc=False)
                .limit(300)
                .execute()
            )
            return response.data or []
        except Exception:
            logger.exception("dedup: candidate pre-filter failed")
            return []

    # -- combined ----------------------------------------------------------

    def check(
        self,
        headline: str,
        canonical_url: str | None = None,
    ) -> DuplicateVerdict:
        """
        Full resolution for one incoming article.

        Order matters: the in-run map is consulted first because a
        syndicated wire story usually arrives twice inside one poll
        cycle, and hitting that costs no network round trip.
        """
        fingerprint = token_fingerprint(headline)

        local = self.check_local(headline, fingerprint, canonical_url)
        if local.is_duplicate:
            self.stats["fingerprint"] += 1
            return local

        try:
            verdict = self.check_database(headline, canonical_url, fingerprint)
        except Exception:
            # Dedup could not run. Treat the article as new rather than
            # dropping it — the unique index on content_hash is still the
            # final backstop against exact duplicates.
            logger.warning("dedup: falling through to insert after lookup failure")
            verdict = UNIQUE

        if not verdict.is_duplicate:
            self.stats["unique"] += 1
            self.remember(verdict, headline, fingerprint, canonical_url)

        return verdict

    def summary(self) -> str:
        total = sum(self.stats.values()) or 1
        parts = [f"{key}={value}" for key, value in self.stats.items() if value]
        return f"{'; '.join(parts)} (of {total})"


# ---------------------------------------------------------------------------
# Cleanliness scoring for merge decisions
# ---------------------------------------------------------------------------

def cleanliness_score(article: dict) -> float:
    """
    Rank candidate versions of the same story so the best one is kept.

    A wire copy with no image and an empty excerpt should lose to the
    outlet's own version that has both. Ordered by how much each signal
    improves what the reader actually sees.
    """
    score = 0.0

    if article.get("featured_image_url") or article.get("image_highres_url"):
        score += 40
    if article.get("video_embed_url") or article.get("video_url"):
        score += 25
    if article.get("audio_url"):
        score += 10

    excerpt = (article.get("excerpt") or "").strip()
    score += min(len(excerpt) / 20.0, 30.0)  # saturates around 600 chars

    if article.get("word_count"):
        score += min(article["word_count"] / 100.0, 20.0)

    if article.get("published_at"):
        score += 10

    # A named outlet beats an anonymous syndication.
    if article.get("source_name_snapshot"):
        score += 5

    return score


def pick_cleanest(candidates: list[dict]) -> dict | None:
    """
    Choose the single version to display from a set of syndicated copies.

    Ties break toward the earliest published version so the timestamp on
    the card reflects when the story actually broke, not when the last
    outlet reposted it.
    """
    if not candidates:
        return None

    def key(article: dict) -> tuple[float, str]:
        published = article.get("published_at") or "9999"
        return (-cleanliness_score(article), published)

    return sorted(candidates, key=key)[0]
"""
Tests for fuzzy syndication detection.

Stage 1 shipped two tests whose MagicMock chain returned the same data
for every lookup tier, so they passed without exercising any of the
matching logic. These use the real functions.

The thresholds asserted here were tuned against actual Nepali wire
headlines; changing one without re-measuring will reintroduce either
duplicate stories or lost ones.
"""

from __future__ import annotations

import pytest

from app.services.dedup import (
    Deduplicator,
    DuplicateVerdict,
    content_hash,
    normalize_title,
    similarity,
    title_tokens,
    token_fingerprint,
)


# =============================================================
# Normalisation
# =============================================================

def test_normalize_strips_outlet_suffix():
    assert normalize_title("Cabinet approves metro rail - OnlineKhabar") == (
        "cabinet approves metro rail"
    )


def test_normalize_is_case_and_whitespace_insensitive():
    assert normalize_title("  Nepal   Wins   ") == normalize_title("nepal wins")


def test_normalize_keeps_devanagari():
    assert normalize_title("नेपालले जित्न सफल भयो") == "नेपालले जित्न सफल भयो"


def test_normalize_empty_input():
    assert normalize_title(None) == ""
    assert normalize_title("") == ""


# =============================================================
# Token fingerprint
# =============================================================

def test_token_fingerprint_is_word_order_independent():
    a = token_fingerprint("Cabinet approves Rs 500 crore for metro rail")
    b = token_fingerprint("Metro rail Rs 500 crore for cabinet approves")
    assert a == b


def test_token_fingerprint_differs_for_different_stories():
    a = token_fingerprint("Cabinet approves Rs 500 crore for metro rail")
    b = token_fingerprint("Nepal wins cricket match against Canada")
    assert a != b


def test_token_fingerprint_not_empty_for_stopword_only_headline():
    # A headline made only of stopwords must not fingerprint to "",
    # or every such headline would collapse into one.
    assert token_fingerprint("the of and to") != ""


def test_stopwords_are_dropped_so_topical_overlap_does_not_match():
    left = title_tokens("Nepal government announces new policy")
    right = title_tokens("Nepal government announces new policy today")
    assert "the" not in left and "today" not in left


# =============================================================
# Similarity — the threshold that matters
# =============================================================

@pytest.mark.parametrize(
    "left,right,expected",
    [
        # A syndicated pair. Must be >= threshold so it merges.
        (
            "Cabinet approves Rs 500 crore budget for metro rail",
            "Rs 500 crore approved for metro rail in cabinet meeting",
            True,
        ),
        (
            "Cabinet clears 500 crore rupees for metro rail project",
            "Metro rail: Rs 500 crore for cabinet",
            True,
        ),
        # Unrelated stories. Must stay below threshold.
        (
            "Cabinet approves Rs 500 crore budget for metro rail",
            "President greets Japanese ambassador at Rastriya Durbar",
            False,
        ),
        (
            "Nepal wins cricket match against Canada",
            "Riverside bridge opens to traffic after repairs",
            False,
        ),
    ],
)
def test_similarity_separates_syndicated_from_unrelated(left, right, expected):
    score = similarity(left, right)
    is_same = score >= 78  # Settings.fuzzy_title_threshold
    assert is_same is expected, f"score was {score:.1f}"


def test_similarity_ignores_topical_overlap_between_different_nepali_sentences():
    """
    Regression guard.

    Two Nepali sentences about the same temple festival share almost all
    their vocabulary. fuzz.partial_ratio scored them 75.8, which at the
    story threshold merged distinct stories and collapsed a 35-sentence
    article into one summary point. partial_ratio must stay out.
    """
    a = "बडादसैँको आठौँ दिन महाअष्टमीका अवसरमा काठमाडौँको मैतीदेवी मन्दिरमा बिहानैदेखि दर्शनार्थीको भीड"
    b = "काठमाडौँको मैतीदेवी मन्दिरमा आज दर्शनार्थीको उल्लेखनीय भीड देखियो।"
    assert similarity(a, b) < 78


def test_similarity_handles_empty_input():
    assert similarity("", "anything") == 0.0
    assert similarity("anything", None) == 0.0


def test_identical_titles_score_100():
    assert similarity("Same headline", "Same headline") == 100.0


# =============================================================
# Content hash
# =============================================================

def test_content_hash_is_url_scoped_but_fingerprint_is_not():
    """
    The content hash includes the URL, so two outlets carrying the same
    story get different hashes. That is why the fuzzy tier exists and is
    not optional: exact matching cannot collapse syndication.
    """
    a = content_hash("https://a.com/1", "Cabinet approves Rs 500 crore for metro rail")
    b = content_hash("https://b.com/2", "Cabinet approves Rs 500 crore for metro rail")
    assert a != b  # URLs differ -> hashes differ
    assert (
        token_fingerprint("Cabinet approves Rs 500 crore for metro rail")
        == token_fingerprint("Cabinet approves Rs 500 crore for metro rail")
    )  # fingerprints match -> fuzzy tier catches it


def test_content_hash_is_stable():
    assert content_hash("https://a.com/1", "Same") == content_hash(
        "https://a.com/1", "Same"
    )


# =============================================================
# Deduplicator in-run behaviour (no database needed)
# =============================================================

def test_in_run_dedup_collapses_reworded_duplicate():
    dedup = Deduplicator(db=None)

    first = dedup.check("Cabinet approves Rs 500 crore budget for metro rail")
    assert first.is_duplicate is False

    second = dedup.check("Rs 500 crore approved for metro rail in cabinet meeting")
    assert second.is_duplicate is True
    assert second.existing_id is None  # no DB, so no id to link to


def test_in_run_dedup_keeps_distinct_stories():
    dedup = Deduplicator(db=None)

    dedup.check("Cabinet approves Rs 500 crore budget for metro rail")
    other = dedup.check("President greets Japanese ambassador at Rastriya Durbar")

    assert other.is_duplicate is False


def test_in_run_dedup_matches_identical_headline():
    dedup = Deduplicator(db=None)

    dedup.check("Nepal wins cricket match against Canada")
    again = dedup.check("Nepal wins cricket match against Canada")

    assert again.is_duplicate is True


def test_deduplicator_counts_verdicts():
    dedup = Deduplicator(db=None)
    dedup.check("A completely distinct story about flooding")
    dedup.check("Another unrelated story about a bridge opening")

    assert dedup.stats["unique"] == 2


def test_check_with_no_database_still_dedups_within_the_run():
    """A database outage must not turn off dedup entirely."""
    dedup = Deduplicator(db=None)

    dedup.check("Cabinet approves Rs 500 crore budget for metro rail")
    dup = dedup.check("Cabinet approves Rs 500 crore budget for metro rail")

    assert isinstance(dup, DuplicateVerdict)
    assert dup.is_duplicate is True

# =============================================================
# In-run dedup has to carry the stored article's id
# =============================================================

def test_in_run_duplicate_carries_the_stored_article_id_after_binding():
    """
    The bug this pins: remember() runs inside check(), before the row is
    written, so the in-run verdict kept existing_id=None. The caller in
    run_source_sync() requires an id before honouring a duplicate, so a
    syndicated copy polled in the same pass fell through and was stored
    as a second article — the exact thing dedup exists to prevent.
    """
    headline = "Cabinet approves Rs 500 crore budget for metro rail"
    stored_id = "11111111-1111-1111-1111-111111111111"

    dedup = Deduplicator()

    first = dedup.check(headline=headline, canonical_url="https://a.example/1")
    assert first.is_duplicate is False

    # The same story from a second outlet, before the id is bound.
    unbound = dedup.check(headline=headline, canonical_url="https://b.example/9")
    assert unbound.is_duplicate is True
    assert unbound.existing_id is None, "expected pre-bind state"

    dedup.bind_article_id(
        headline=headline,
        fingerprint=token_fingerprint(headline),
        url="https://a.example/1",
        article_id=stored_id,
    )

    bound = dedup.check(headline=headline, canonical_url="https://c.example/3")
    assert bound.is_duplicate is True
    assert bound.existing_id == stored_id


def test_bind_article_id_does_not_create_entries():
    """Binding must only update what remember() already recorded."""
    dedup = Deduplicator()
    dedup.bind_article_id(
        headline="A story that was never seen this run",
        fingerprint="deadbeef",
        url="https://nowhere.example/1",
        article_id="11111111-1111-1111-1111-111111111111",
    )
    assert dedup._local == {}
    assert dedup._token_index == {}


def test_bind_article_id_ignores_an_empty_id():
    headline = "Cabinet approves Rs 500 crore budget for metro rail"
    dedup = Deduplicator()
    dedup.check(headline=headline, canonical_url="https://a.example/1")

    dedup.bind_article_id(
        headline=headline,
        fingerprint=token_fingerprint(headline),
        url="https://a.example/1",
        article_id="",
    )
    verdict = dedup.check(headline=headline, canonical_url="https://d.example/4")
    assert verdict.existing_id is None

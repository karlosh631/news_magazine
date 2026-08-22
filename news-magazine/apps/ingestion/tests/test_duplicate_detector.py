from unittest.mock import MagicMock

from app.services.duplicate_detector import DuplicateDetector
from app.sources.base import NormalizedArticle


def _make_query_mock(return_data):
    m = MagicMock()
    m.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = return_data
    m.table.return_value.select.return_value.gte.return_value.execute.return_value.data = []
    return m


def test_exact_content_hash_match_is_duplicate():
    existing = [{"id": "abc-123", "headline": "Existing", "canonical_url": "https://example.com/a"}]
    db = _make_query_mock(existing)
    detector = DuplicateDetector(db)

    article = NormalizedArticle(
        source_article_url="https://example.com/a?utm=1",
        canonical_url="https://example.com/a",
        headline="Existing story",
        published_at=None,
        raw_metadata={"content_hash": "deadbeef", "title_normalized": "existing story"},
    )

    result = detector.find_existing(article)
    assert result is not None
    assert result["id"] == "abc-123"


def test_no_match_returns_none():
    db = _make_query_mock([])
    detector = DuplicateDetector(db)

    article = NormalizedArticle(
        source_article_url="https://example.com/new",
        canonical_url="https://example.com/new",
        headline="Brand new story nobody has seen",
        published_at=None,
        raw_metadata={"content_hash": "freshhash", "title_normalized": "brand new story nobody has seen"},
    )

    result = detector.find_existing(article)
    assert result is None

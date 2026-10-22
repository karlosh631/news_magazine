"""
The wrong-picture rules, on the write side.

THE BUG THIS EXISTS FOR
-----------------------
A reader sent a screenshot of a movie review illustrated with the
byline photograph of the person who wrote it. The image was a byline
portrait: `.../uploads/source/author/s/samarpan....jpg`, which the
junk check REJECTS.

So the junk check had done its job, and the image was stored anyway.
The reason is in the second writer of `featured_image_url` -
`search_resurrect._build_row`:

    image = None
    if extracted is not None:
        image = extracted.image_highres_url or extracted.image_url
    if not image:
        image = getattr(article, "thumbnail_url", None)
    if live_image is not None:
        image = live_image

`live_image` is None PRECISELY WHEN the junk check rejected every
candidate. So the fallback fired exactly when it should not have, and
stored the URL that had just been rejected. The comment above it even
says "Validation overrides the guess" - it only did so when validation
SUCCEEDED.

These specs pin the corrected behaviour: when validation ran, its
answer is the only answer.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.search_resurrect import OnDemandSearcher


def _article(**over):
    base = dict(
        headline="Movie review: 12 Gaun 2 delivers the fights",
        excerpt="An excerpt.",
        language="ne",
        source_article_url="https://kathmandupost.com/movie-review/x",
        canonical_url=None,
        published_at=None,
        raw_metadata={},
        thumbnail_url="https://cdn.test/feed-thumb.jpg",
    )
    base.update(over)
    return SimpleNamespace(**base)


AUTHOR_PORTRAIT = (
    "https://assets-cdn.kathmandupost.com/uploads/source/author/s/"
    "samarpan161120240301221000x0-1789043092.jpg"
)
REAL_PHOTOGRAPH = (
    "https://assets-cdn.kathmandupost.com/uploads/source/news/2026/"
    "third-party/birajphoto201020261116181000x0-1792492779.jpg"
)


def _service():
    return OnDemandSearcher.__new__(OnDemandSearcher)


def _extracted(image_url):
    return SimpleNamespace(
        image_url=image_url,
        image_highres_url=image_url,
        image_candidates=[image_url] if image_url else [],
        declared_images=[image_url] if image_url else [],
        description="A description.",
        title=None,
        video_embed_url=None,
        audio_url=None,
        word_count=100,
    )


class TestValidationWins:
    def test_a_rejected_image_is_not_stored(self):
        """
        The regression in one assertion.

        The junk check rejects the byline portrait, so the candidate
        list is empty, so `live_image` is None - and the old code fell
        back to the raw URL that had just been rejected.
        """
        service = _service()

        row = service._build_row(
            _article(),
            _extracted(AUTHOR_PORTRAIT),
            "movie review",
            live_image=None,
            image_validated=True,
        )

        assert row["featured_image_url"] is None
        assert row["image_highres_url"] is None

    def test_a_proven_image_is_stored(self):
        service = _service()

        row = service._build_row(
            _article(),
            _extracted(REAL_PHOTOGRAPH),
            "movie review",
            live_image=REAL_PHOTOGRAPH,
            image_validated=True,
        )

        assert row["featured_image_url"] == REAL_PHOTOGRAPH
        assert row["image_highres_url"] == REAL_PHOTOGRAPH

    def test_a_validated_image_is_marked_verified(self):
        service = _service()

        row = service._build_row(
            _article(),
            _extracted(REAL_PHOTOGRAPH),
            "movie review",
            live_image=REAL_PHOTOGRAPH,
            image_validated=True,
        )

        assert row["image_verified_at"] is not None


class TestWithoutValidation:
    def test_the_raw_url_is_still_used_when_nothing_checked_it(self):
        """
        When validation did not run, the raw URL is the only evidence
        there is. Using it is right - the row is simply left UNVERIFIED,
        so the repair pass comes back for it later.
        """
        service = _service()

        row = service._build_row(
            _article(),
            _extracted(REAL_PHOTOGRAPH),
            "movie review",
            live_image=None,
            image_validated=False,
        )

        assert row["featured_image_url"] == REAL_PHOTOGRAPH
        assert row["image_verified_at"] is None

    def test_the_feed_thumbnail_is_the_last_resort(self):
        service = _service()

        row = service._build_row(
            _article(),
            None,
            "movie review",
            live_image=None,
            image_validated=False,
        )

        assert row["featured_image_url"] == "https://cdn.test/feed-thumb.jpg"
        assert row["image_verified_at"] is None


class TestTheFeedThumbnailIsNotABackdoor:
    def test_a_rejected_feed_thumbnail_is_not_stored(self):
        """
        The feed thumbnail was the other way in. It is a raw URL with
        no junk check on it at all, and it was the fallback when the
        page's own image was rejected.
        """
        service = _service()

        row = service._build_row(
            _article(thumbnail_url=AUTHOR_PORTRAIT),
            _extracted(AUTHOR_PORTRAIT),
            "movie review",
            live_image=None,
            image_validated=True,
        )

        assert row["featured_image_url"] is None
        assert row["image_highres_url"] is None


@pytest.mark.parametrize(
    "live_image",
    [None, REAL_PHOTOGRAPH],
)
def test_the_row_always_has_the_image_columns(live_image):
    """Whatever happens, the two columns exist and agree."""
    service = _service()

    row = service._build_row(
        _article(),
        _extracted(REAL_PHOTOGRAPH),
        "movie review",
        live_image=live_image,
        image_validated=True,
    )

    assert "featured_image_url" in row
    assert "image_highres_url" in row
    assert row["featured_image_url"] == row["image_highres_url"]


class TestArrivalTime:
    """
    A RESURRECTED STORY DOES NOT ARRIVE AT THE TOP OF THE FEED
    -------------------------------------------------------
    The feed is ordered by `ingested_at` - when we FIRST saw the
    story. The resurrection path was stamping that column with the
    current time, so a story published in January 2025, pulled back
    out of the database because somebody searched for it, arrived with
    a brand-new arrival time and jumped to the very top of the
    homepage, above the news of the day.

    That is the wrong answer to the question the feed answers. A
    resurrected story is an OLD story that has been re-fetched on
    demand, and putting it at the top says otherwise.
    """

    def test_a_resurrected_story_keeps_its_publication_time_as_its_arrival(self):
        service = _service()
        published = "2025-01-02T13:30:00+00:00"

        row = service._build_row(
            _article(published_at=published),
            _extracted(REAL_PHOTOGRAPH),
            "movie review",
            live_image=REAL_PHOTOGRAPH,
            image_validated=True,
        )

        assert row["ingested_at"] == published
        assert row["published_at"] == published

    def test_the_re_fetch_is_still_recorded(self):
        """
        "We just re-fetched this" is worth knowing. It is simply not
        what the feed orders by.
        """
        service = _service()
        published = "2025-01-02T13:30:00+00:00"

        row = service._build_row(
            _article(published_at=published),
            _extracted(REAL_PHOTOGRAPH),
            "movie review",
            live_image=REAL_PHOTOGRAPH,
            image_validated=True,
        )

        assert row["resurrected_at"] is not None
        assert row["last_seen_at"] is not None
        assert row["ingested_at"] != row["resurrected_at"]

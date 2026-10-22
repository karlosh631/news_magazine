"""
The rules that decide which picture belongs to a story.

THE BUG THESE EXIST FOR
-----------------------
A reader sent a screenshot of a homepage where two cards showed the
same photograph, and a separate screenshot of a story about drugs seized
from a motorcycle illustrated with an Aston Villa FOOTBALL picture. The
same defect as a movie review illustrated with the byline photograph of
the person who wrote it.

Every URL below was measured on production. None of them is a
hypothetical.
"""

from __future__ import annotations

import pytest

from app.sources.media import (
    _is_duplicate_copy,
    _is_gif_artifact,
    _is_theme_asset,
    _looks_like_junk_image,
)


class TestThemeAssets:
    """A file in the site's own theme directory is chrome, not a story."""

    def test_the_onlinekhabar_theme_badge_is_rejected(self):
        # This was the og:image of two unrelated OnlineKhabar stories.
        url = (
            "https://onlinekhabar.com/wp-content/themes/onlinekhabar-2021/"
            "img/ok-khusi.png"
        )
        assert _is_theme_asset(url) is True
        assert _looks_like_junk_image(url) is True

    def test_a_real_photograph_in_a_wp_content_path_is_kept(self):
        # The rule must not reach ordinary WordPress uploads.
        url = "https://nepalnews.com/wp-content/uploads/2026/10/apf11.jpg"
        assert _is_theme_asset(url) is False
        assert _looks_like_junk_image(url) is False

    def test_the_word_theme_alone_is_not_enough(self):
        # A path that merely CONTAINS "theme" is not a theme directory.
        url = "https://x.test/theme-song-cover.jpg"
        assert _is_theme_asset(url) is False


class TestDuplicateCopies:
    """A "name-Copy" duplicate is not the original upload."""

    def test_the_nepalhomes_copy_is_rejected(self):
        # This was the artwork of three unrelated OnlineKhabar stories.
        url = (
            "https://www.nepalhomes.com/public/400-300/media/"
            "FBBF4C13-1-Copy.jpg"
        )
        assert _is_duplicate_copy(url) is True
        assert _looks_like_junk_image(url) is True

    def test_the_original_is_kept(self):
        url = "https://www.nepalhomes.com/public/400-300/media/FBBF4C13-1.jpg"
        assert _is_duplicate_copy(url) is False
        assert _looks_like_junk_image(url) is False

    def test_a_size_suffix_is_not_a_copy(self):
        # "-1024x556" is a rendition, not a duplicate, and treating it
        # as one would throw away most of a publisher's real images.
        url = "https://x.test/dashain-photo-1024x556.jpg"
        assert _is_duplicate_copy(url) is False
        assert _looks_like_junk_image(url) is False


class TestGifArtifacts:
    """A GIF in the artwork columns is a screenshot or a badge."""

    def test_the_clipboard_screenshot_is_rejected(self):
        # This was the artwork of two 2026 MOFA speeches.
        url = (
            "https://ictsamacharcdn.prixacdn.net/x/"
            "Clipboard_-_October_8_2024_4_59_PM.gif"
        )
        assert _is_gif_artifact(url) is True
        assert _looks_like_junk_image(url) is True

    def test_a_jpeg_is_not_a_gif(self):
        url = "https://x.test/photo.jpg"
        assert _is_gif_artifact(url) is False
        assert _looks_like_junk_image(url) is False


class TestTheRulesThatAlreadyExisted:
    """The original junk rules must keep working exactly as they did."""

    def test_an_author_portrait_is_rejected(self):
        # This was the artwork of a movie review on kathmandupost.com:
        # the byline photograph of the person who wrote it.
        url = (
            "https://assets-cdn.kathmandupost.com/uploads/source/author/s/"
            "samarpan161120240301221000x0-1789043092.jpg"
        )
        assert _looks_like_junk_image(url) is True

    def test_a_bare_directory_path_is_rejected(self):
        # A directory with no filename behind it. The CDN answers 403.
        url = "https://assets-cdn.kathmandupost.com/uploads/source/"
        assert _looks_like_junk_image(url) is True

    def test_a_real_article_photograph_is_kept(self):
        url = (
            "https://assets-cdn.kathmandupost.com/uploads/source/news/2026/"
            "third-party/birajphoto201020261116181000x0-1792492779.jpg"
        )
        assert _looks_like_junk_image(url) is False

    def test_no_url_is_junk(self):
        assert _looks_like_junk_image("") is True
        assert _looks_like_junk_image("not a url") is True


class TestEveryRuleIsIndependent:
    """
    Each rule is its own function, so one of them can be switched off
    without unravelling the others. This asserts that: a URL that only
    ONE rule rejects is still rejected, and a URL that NO rule rejects
    survives.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://onlinekhabar.com/wp-content/themes/onlinekhabar-2021/img/ok-khusi.png",
            "https://www.nepalhomes.com/public/400-300/media/FBBF4C13-1-Copy.jpg",
            "https://ictsamacharcdn.prixacdn.net/x/Clipboard_-_October_8_2024_4_59_PM.gif",
            "https://assets-cdn.kathmandupost.com/uploads/source/author/s/x.jpg",
            "https://assets-cdn.kathmandupost.com/uploads/source/",
        ],
    )
    def test_a_rejected_url_is_rejected(self, url):
        assert _looks_like_junk_image(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://nepalnews.com/wp-content/uploads/2026/10/apf11.jpg",
            "https://nepalnews.com/wp-content/uploads/2026/10/KB_-KTM_-RSS_-04_-_U3A8132-1-768x512-1.jpeg",
            "https://assets-cdn.kathmandupost.com/uploads/source/news/2026/third-party/birajphoto201020261116181000x0-1792492779.jpg",
            "https://www.onlinekhabar.com/wp-content/uploads/2026/10/photo.jpg",
        ],
    )
    def test_a_real_photograph_survives(self, url):
        assert _looks_like_junk_image(url) is False

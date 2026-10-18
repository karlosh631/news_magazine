"""
Tests for image extraction.

These pin the three bugs that made the site show wrong or broken
pictures, all of which looked like "the image is wrong" with no
explanation:

  1. An og:image that was a whole srcset string, stored as a URL.
  2. http:// URLs, which work on localhost and break on the https
     production host as mixed content.
  3. A site-wide banner picked up as the article image, so dozens of
     unrelated stories rendered the same picture.
"""

from __future__ import annotations

from app.sources.media import (
    CONTENT_IMAGE_BONUS,
    _clean_url,
    _chrome_spans,
    _inside_any,
    _looks_like_junk_image,
    _score_image,
    upgrade_to_highres,
)


# =============================================================
# URL normalisation
# =============================================================

def test_clean_url_passes_through_a_plain_https_url():
    assert _clean_url("https://cdn.example.com/a.jpg", "https://site.com/") == (
        "https://cdn.example.com/a.jpg"
    )


def test_clean_url_upgrades_http_to_https():
    """
    Mixed content. localhost served over http tolerated these; the
    production host does not, so they rendered as broken images there
    while working in development.
    """
    assert _clean_url("http://cdn.example.com/a.jpg", "https://site.com/") == (
        "https://cdn.example.com/a.jpg"
    )


def test_clean_url_upgrades_protocol_relative():
    assert _clean_url("//cdn.example.com/a.jpg", "https://site.com/") == (
        "https://cdn.example.com/a.jpg"
    )


def test_clean_url_resolves_relative():
    assert _clean_url("/media/a.jpg", "https://site.com/news/x") == (
        "https://site.com/media/a.jpg"
    )


def test_clean_url_extracts_first_candidate_from_a_srcset():
    """
    Several outlets put an entire srcset into og:image. Passing that
    through produced a URL containing a space and a comma.
    """
    srcset = "https://a.com/photo.jpg 2560w, https://a.com/photo-1200.jpg 1200w"
    assert _clean_url(srcset, "https://site.com/") == "https://a.com/photo.jpg"


def test_clean_url_extracts_from_srcset_without_spaces_after_comma():
    srcset = "https://a.com/photo.jpg 800w,https://a.com/photo-1600.jpg 1600w"
    assert _clean_url(srcset, "https://site.com/") == "https://a.com/photo.jpg"


def test_clean_url_rejects_unrendered_template_placeholders():
    """
    Client-rendered pages ship template source, so an <img> can have
    src="{{data.tags[i].trending_image}}". That is not a URL.
    """
    for bad in (
        "{{data.tags[i].trending_image}}",
        "https://a.com/{{img}}.jpg",
        "https://a.com/${slug}.jpg",
        "https://a.com/[[img]].jpg",
    ):
        assert _clean_url(bad, "https://site.com/") is None, bad


def test_clean_url_rejects_non_http_schemes():
    assert _clean_url("javascript:alert(1)", "https://site.com/") is None
    assert _clean_url("data:text/html,<script>", "https://site.com/") is None


def test_clean_url_rejects_empty():
    assert _clean_url("", "https://site.com/") is None
    assert _clean_url(None, "https://site.com/") is None


# =============================================================
# Junk detection
# =============================================================

def test_junk_image_detection_catches_furniture():
    for junk in (
        "https://a.com/assets/logo.png",
        "https://a.com/img/pixel.gif",
        "https://a.com/spacer-1x1.png",
        "https://a.com/placeholder.jpg",
        "https://a.com/icons/avatar-large.png",
    ):
        assert _looks_like_junk_image(junk), junk


def test_real_photo_is_not_flagged_as_junk():
    assert not _looks_like_junk_image(
        "https://a.com/2026/10/Mahagauri-Devi-1024x576.jpg"
    )


# =============================================================
# Scoring
# =============================================================

def test_larger_declared_dimensions_score_higher():
    small = _score_image("https://a.com/x.jpg", 320, 180)
    large = _score_image("https://a.com/x.jpg", 1200, 675)
    assert large > small


def test_url_size_token_is_used_when_no_dimensions_declared():
    small = _score_image("https://a.com/x-320x180.jpg", None, None)
    large = _score_image("https://a.com/x-1200x675.jpg", None, None)
    assert large > small


def test_content_image_bonus_outranks_a_declared_og_image():
    """
    The bug: an outlet sets og:image to its channel logo, so every story
    rendered the same picture. A photograph found in the body must win
    even when the declared og:image is larger.
    """
    og_score = _score_image("https://a.com/logo-1200x675.jpg", 1200, 675)
    body_score = _score_image("https://a.com/story.jpg", None, None) + CONTENT_IMAGE_BONUS
    assert body_score > og_score


# =============================================================
# Chrome span detection
# =============================================================

def test_chrome_spans_cover_banner_and_related_blocks():
    html = (
        '<div class="banner-ad"><img src="https://a.com/ad.jpg"></div>'
        '<div class="related-stories"><img src="https://a.com/rel.jpg"></div>'
        '<article><img src="https://a.com/story.jpg"></article>'
    )
    spans = _chrome_spans(html)
    assert len(spans) == 2


def test_image_inside_a_banner_is_marked_as_chrome():
    html = (
        '<div class="ad-banner"><img src="https://a.com/ad.jpg"></div>'
        '<article><img src="https://a.com/story.jpg"></article>'
    )
    spans = _chrome_spans(html)

    ad = html.index("https://a.com/ad.jpg")
    story = html.index("https://a.com/story.jpg")

    assert _inside_any((ad, ad + 10), spans)
    assert not _inside_any((story, story + 10), spans)


def test_no_chrome_spans_on_a_clean_page():
    html = '<article><p>Text</p><img src="https://a.com/story.jpg"></article>'
    assert _chrome_spans(html) == []


def test_chrome_detection_survives_unbalanced_html():
    """
    The page does not have to be well-formed. Offsets are computed by
    walking tags rather than by deleting markup, so a missing close tag
    cannot corrupt the rest of the document — which is what an earlier
    regex-based approach did to Kathmandu Post.
    """
    html = '<div class="sidebar"><span>unclosed<div><img src="https://a.com/x.jpg">'
    spans = _chrome_spans(html)
    assert isinstance(spans, list)


# =============================================================
# High-res upgrade
# =============================================================

def test_upgrade_to_highres_rewrites_a_size_suffix():
    assert upgrade_to_highres("https://a.com/x-320x180.jpg") == (
        "https://a.com/x-1200x675.jpg"
    )


def test_upgrade_to_highres_leaves_plain_urls_alone():
    plain = "https://a.com/2026/10/story.jpg"
    assert upgrade_to_highres(plain) == plain


def test_upgrade_to_highres_handles_none():
    assert upgrade_to_highres(None) is None
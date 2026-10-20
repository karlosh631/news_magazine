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

and, below, the fourth: an image that was never loadable in the first
place. Every other check here judges a URL by its shape. None of them
asks the CDN whether the URL works, so a row could be written carrying
a URL that 403s forever and the only party that ever found out was a
reader's browser. These tests use httpx's MockTransport, so nothing
here touches the network.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.ingestion import merge_media, verify_media_images
from app.sources.media import (
    CONTENT_IMAGE_BONUS,
    RENDERABLE_IMAGE_TYPES,
    ExtractedMedia,
    ImageValidator,
    _clean_url,
    _chrome_spans,
    _inside_any,
    _is_resizer,
    _looks_like_junk_image,
    _score_image,
    best_image,
    candidate_images,
    resolve_live_image,
    strip_size_suffix,
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

# =============================================================
# Thumbnail proxies
#
# Kathmandu Post serves body images through
# assets-api.../thumb.php?src=<real file>. A -4000 score penalty was not
# enough to beat it, the proxy won, and because it is derived from a
# handful of shared endpoints the SAME picture landed on eight unrelated
# cards. A resizer can only ever return a downscaled version of
# something else, so it is now disqualifying rather than ranked down.
# =============================================================

def test_thumbnail_proxy_is_disqualified():
    assert _is_resizer(
        "https://assets-api.kathmandupost.com/thumb.php?src=https://x/a.jpg"
    )
    assert _is_resizer("https://cdn.example.com/image-resize/800/a.jpg")
    assert _is_resizer("https://news.example.com/cdn-cgi/image/width=800/a.jpg")
    assert _is_resizer("https://wsrv.nl/?url=example.com/a.jpg")


def test_a_real_image_is_not_mistaken_for_a_resizer():
    """
    The list has to stay narrow. A blanket `/cache/` exclusion was tried
    and rejected: real CDN paths contain it (Cloudflare's
    /cdn-cgi/cache/ among them), and discarding those would manufacture
    exactly the blank cards this change exists to prevent.
    """
    assert not _is_resizer("https://cdn.example.com/cdn-cgi/cache/images/a.jpg")
    assert not _is_resizer("https://example.com/media/cache/abc/photo.jpg")
    assert not _is_resizer(
        "https://ictsamacharcdn.prixacdn.net/media/albums/photo.jpg"
    )
    assert not _is_resizer("https://cdn.example.com/wp-content/uploads/a.jpg")


def test_a_resizer_scores_below_every_real_candidate():
    proxy = "https://assets-api.kathmandupost.com/thumb.php?src=https://x/a.jpg"
    real = "https://cdn.example.com/wp-content/uploads/a.jpg"
    assert _score_image(proxy, 1200, 800) < _score_image(real, 400, 300)


def test_best_image_falls_through_a_proxy_to_the_real_feed_image():
    """The page's proxy loses; the feed's direct CDN URL wins."""
    page = ExtractedMedia(image_url="https://assets-api.kathmandupost.com/thumb.php?src=x")
    assert best_image("https://assets-cdn.kathmandupost.com/real.jpg", page) == (
        "https://assets-cdn.kathmandupost.com/real.jpg"
    )


def test_best_image_returns_none_when_only_a_proxy_exists():
    """
    No image beats a downscaled stand-in. The card has an honest
    "no image available" state, and the feed falls back to another
    illustrated article rather than repeating this one.
    """
    page = ExtractedMedia(image_url="https://assets-api.kathmandupost.com/thumb.php?src=x")
    assert best_image("https://other.test/thumbnail.php?src=y", page) is None


def test_best_image_prefers_the_page_image_when_both_are_real():
    page = ExtractedMedia(image_url="https://cdn.example.com/og.jpg")
    assert best_image("https://cdn.example.com/feed.jpg", page) == (
        "https://cdn.example.com/og.jpg"
    )


# =============================================================
# Image liveness
#
# The bug these pin: an image URL was chosen on the server by ranking
# the URL's shape, then stored without anyone asking whether it loads.
# assets-cdn.kathmandupost.com answers 403 to a referrer it does not
# recognise, so a stored URL could be permanently dead. The web card
# had to discover that in the reader's browser, flashing a broken image
# and walking a fallback chain one failed <img> at a time.
#
# Every request below is served by httpx.MockTransport. No test in this
# file reaches the network.
# =============================================================

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 512
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 512


def build(status=200, content_type="image/jpeg", body=JPEG_BYTES, headers=None):
    """
    A response factory.

    A factory rather than a Response object because httpx consumes the
    body, so one instance cannot be served to two requests.
    """
    def make(request: httpx.Request | None = None) -> httpx.Response:
        merged = {"content-type": content_type}
        merged.update(headers or {})
        return httpx.Response(status, headers=merged, content=body)

    return make


def transport(routes: dict, seen: list | None = None) -> httpx.MockTransport:
    """
    Route by path. Anything unrouted 404s, which is what a real CDN
    does to a file that was never published.

    Records every request in `seen` so a test can assert on the method
    and headers actually sent.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, text="not found")
        return route(request)

    return httpx.MockTransport(handler)


async def is_live(url: str, routes: dict, seen: list | None = None) -> bool:
    async with httpx.AsyncClient(transport=transport(routes, seen)) as client:
        return await ImageValidator(client).is_live(url)


# --- what counts as loadable ---

async def test_a_200_image_jpeg_is_live():
    assert await is_live(
        "https://cdn.test/photo.jpg", {"/photo.jpg": build()}
    )


async def test_a_206_partial_response_is_live():
    """
    206 is the correct answer to the ranged GET this validator sends.
    Treating it as a failure would reject every image on a server that
    honours Range — i.e. most of them.
    """
    assert await is_live(
        "https://cdn.test/photo.jpg",
        {"/photo.jpg": build(status=206, headers={"content-length": "1024"})},
    )


async def test_a_403_is_not_live():
    """
    The actual failure being fixed. assets-cdn.kathmandupost.com answers
    403 Forbidden for referrers it does not recognise, so this URL was
    stored and rendered as a broken image on every card that used it.
    """
    assert not await is_live(
        "https://assets-cdn.kathmandupost.com/photo.jpg",
        {"/photo.jpg": build(status=403, content_type="text/html",
                              body=b"<html>Forbidden</html>")},
    )


async def test_a_404_is_not_live():
    assert not await is_live(
        "https://cdn.test/gone.jpg", {"/gone.jpg": build(status=404)}
    )


@pytest.mark.parametrize("code", [400, 401, 429, 500, 502, 503])
async def test_any_non_2xx_is_not_live(code):
    assert not await is_live(
        "https://cdn.test/photo.jpg", {"/photo.jpg": build(status=code)}
    )


async def test_a_200_that_is_not_an_image_is_not_live():
    """
    A 200 is not proof. A CDN serving an HTML error or a JSON manifest
    with a success code still renders as a broken card, so the
    Content-Type has to be checked, not just the status.
    """
    assert not await is_live(
        "https://cdn.test/photo.jpg",
        {"/photo.jpg": build(content_type="text/html", body=b"<html>hi</html>")},
    )


async def test_a_200_whose_body_is_markup_is_not_live():
    """
    A bot-challenge page served as 200 image/jpeg. Content-Type agrees,
    the status agrees, and the body is an HTML document. Without
    sniffing the first bytes this stores confidently and renders as a
    wall of text where the photograph should be.
    """
    assert not await is_live(
        "https://cdn.test/photo.jpg",
        {
            "/photo.jpg": build(
                content_type="image/jpeg",
                body=b"<!DOCTYPE html><html><body>Are you a robot?</body></html>",
            )
        },
    )


async def test_an_empty_body_is_not_live():
    assert not await is_live(
        "https://cdn.test/photo.jpg", {"/photo.jpg": build(body=b"")}
    )


async def test_a_zero_content_length_is_not_live():
    assert not await is_live(
        "https://cdn.test/photo.jpg",
        {"/photo.jpg": build(body=b"\xff\xd8", headers={"content-length": "0"})},
    )


async def test_a_connection_error_is_not_live_and_never_raises():
    """
    An image CDN that hangs or refuses the socket is an image that does
    not get stored — not a sync that dies. Extraction must never break
    a source poll.
    """
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(
        transport=transport({"/photo.jpg": explode})
    ) as client:
        assert await ImageValidator(client).is_live("https://cdn.test/photo.jpg") is False


async def test_a_timeout_is_not_live_and_never_raises():
    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=transport({"/photo.jpg": hang})) as client:
        assert await ImageValidator(client).is_live("https://cdn.test/photo.jpg") is False


async def test_a_png_is_live():
    assert await is_live(
        "https://cdn.test/photo.png",
        {"/photo.png": build(content_type="image/png", body=PNG_BYTES)},
    )


async def test_content_type_parameters_do_not_break_the_check():
    """CDNs append charset and such; image/jpeg; charset=binary is fine."""
    assert await is_live(
        "https://cdn.test/photo.jpg",
        {"/photo.jpg": build(content_type="image/jpeg; charset=binary")},
    )


def test_svg_is_not_treated_as_renderable_news_artwork():
    """
    An SVG in a news slot is a logo or an ad, not a photograph, and it
    cannot survive the markup sniff below anyway. Listed explicitly so
    the exclusion is a decision rather than an oversight.
    """
    assert "image/svg+xml" not in RENDERABLE_IMAGE_TYPES
    assert "image/jpeg" in RENDERABLE_IMAGE_TYPES


# --- the request must look like the browser's ---

async def test_validation_sends_no_referer():
    """
    The web card fetches with referrerPolicy="no-referrer". Several of
    these CDNs reject a foreign Referer outright, so sending one while
    validating would prove an image dead precisely when it is alive for
    every reader. The check and the fetch must agree.
    """
    seen: list = []
    await is_live("https://cdn.test/photo.jpg", {"/photo.jpg": build()}, seen)
    assert "referer" not in seen[0].headers


async def test_validation_uses_get_not_head():
    """
    CDNs answer HEAD and GET differently often enough that a HEAD-based
    check produces confident false negatives on perfectly good images.
    A ranged GET is what the browser will actually do.
    """
    seen: list = []
    await is_live("https://cdn.test/photo.jpg", {"/photo.jpg": build()}, seen)
    assert seen[0].method == "GET"


async def test_validation_sends_a_range_header():
    """
    This is what bounds the cost. Without it a 200 sends the entire
    image, and validating a poll of thumbnails becomes a bandwidth
    problem rather than a kilobyte one.
    """
    seen: list = []
    await is_live("https://cdn.test/photo.jpg", {"/photo.jpg": build()}, seen)
    assert seen[0].headers["range"] == "bytes=0-1023"


async def test_validation_sends_a_browser_user_agent():
    seen: list = []
    await is_live("https://cdn.test/photo.jpg", {"/photo.jpg": build()}, seen)
    assert "Chrome" in seen[0].headers["user-agent"]


# --- caching: outlets reuse one image across many stories ---

async def test_a_shared_image_is_probed_once_per_poll():
    """
    The reason the validator exists at all is affordable poll cost. An
    outlet reusing one image across ten stories must cost one request,
    not ten, or validation scales with articles instead of with distinct
    images.
    """
    seen: list = []
    routes = {"/logo.png": build(content_type="image/png", body=PNG_BYTES)}

    async with httpx.AsyncClient(transport=transport(routes, seen)) as client:
        validator = ImageValidator(client)
        for _ in range(10):
            assert await validator.is_live("https://cdn.test/logo.png") is True

    assert len(seen) == 1
    assert validator.requests == 1
    assert validator.cache_hits == 9


async def test_a_dead_image_is_remembered_as_dead():
    """
    A 403 does not become a 200 on the next article. Caching only the
    successes would re-request every known-bad URL once per story that
    shares it.
    """
    seen: list = []
    routes = {"/dead.jpg": build(status=403)}

    async with httpx.AsyncClient(transport=transport(routes, seen)) as client:
        validator = ImageValidator(client)
        for _ in range(5):
            assert await validator.is_live("https://cdn.test/dead.jpg") is False

    assert len(seen) == 1


async def test_the_verdict_cache_is_bounded():
    """
    A long poll must not accumulate a verdict for every URL it has
    ever seen. 2048 is far above any real poll's distinct image count,
    so nothing is forgotten while it matters.
    """
    async with httpx.AsyncClient(transport=transport({})) as client:
        validator = ImageValidator(client, cache_size=4)
        for index in range(40):
            await validator.is_live(f"https://cdn.test/{index}.jpg")

    assert len(validator._cache) == 4


async def test_an_empty_url_is_never_live_and_never_requested():
    seen: list = []
    async with httpx.AsyncClient(transport=transport({}, seen)) as client:
        validator = ImageValidator(client)
        assert await validator.is_live(None) is False
        assert await validator.is_live("") is False
    assert seen == []


# --- falling through to the next candidate ---

async def test_a_dead_first_candidate_falls_through_to_a_live_second():
    """
    The requirement: when the chosen image is broken, find the working
    alternate rather than discarding the story's artwork. The outlet
    routinely offers both — a dead og:image alongside a live body <img>.
    """
    seen: list = []
    routes = {
        "/og.jpg": build(status=403),
        "/body.jpg": build(),
    }
    async with httpx.AsyncClient(transport=transport(routes, seen)) as client:
        winner = await resolve_live_image(
            [
                "https://cdn.test/og.jpg",
                "https://cdn.test/body.jpg",
            ],
            ImageValidator(client),
        )

    assert winner == "https://cdn.test/body.jpg"
    # Stopped at the first success. Validating the rest would be two
    # requests per article instead of one.
    assert len(seen) == 2


async def test_resolve_returns_none_when_every_candidate_is_dead():
    routes = {"/a.jpg": build(status=403), "/b.jpg": build(status=404)}
    async with httpx.AsyncClient(transport=transport(routes)) as client:
        assert await resolve_live_image(
            ["https://cdn.test/a.jpg", "https://cdn.test/b.jpg"],
            ImageValidator(client),
        ) is None


async def test_resolve_stops_at_the_candidate_limit():
    """
    A pathological page must not turn one article into an unbounded
    burst of requests at a CDN that is already refusing to serve us.
    """
    seen: list = []
    routes = {f"/{i}.jpg": build(status=403) for i in range(10)}
    candidates = [f"https://cdn.test/{i}.jpg" for i in range(10)]

    async with httpx.AsyncClient(transport=transport(routes, seen)) as client:
        assert await resolve_live_image(
            candidates, ImageValidator(client), limit=3
        ) is None

    assert len(seen) == 3


# --- candidate ordering ---

def test_candidates_offer_the_size_stripped_fallback():
    """
    Mirrors pickImages()/withoutSizeSuffix() in NewsCard.tsx. A dead
    "-1200x675" rendition very often has a live original at its own
    path, and the browser already knew that — this just stops the
    reader having to find out.
    """
    page = ExtractedMedia(
        image_url="https://cdn.test/a-1200x675.jpg",
        image_candidates=["https://cdn.test/a-1200x675.jpg"],
    )
    assert candidate_images(None, page) == [
        "https://cdn.test/a-1200x675.jpg",
        "https://cdn.test/a.jpg",
    ]


def test_real_renditions_are_offered_before_stripped_guesses():
    """
    The stripped form is a fallback, not the favourite. Offering it
    first would replace a real 1200px photograph with whatever unsized
    original the CDN happens to have.
    """
    page = ExtractedMedia(
        image_url="https://cdn.test/a-1200x675.jpg",
        image_candidates=["https://cdn.test/a-1200x675.jpg"],
    )
    candidates = candidate_images("https://cdn.test/b-320x180.jpg", page)
    assert candidates[:2] == [
        "https://cdn.test/a-1200x675.jpg",
        "https://cdn.test/b-320x180.jpg",
    ]


def test_the_full_page_ranking_is_offered_not_just_the_winner():
    """
    extract_from_article used to keep only the best image, so a dead
    winner cost the article its artwork even when the page had offered
    two live alternates. The whole ranking is kept for this reason.
    """
    page = ExtractedMedia(
        image_url="https://cdn.test/best.jpg",
        image_candidates=[
            "https://cdn.test/best.jpg",
            "https://cdn.test/second.jpg",
            "https://cdn.test/third.jpg",
        ],
    )
    assert candidate_images(None, page)[:3] == [
        "https://cdn.test/best.jpg",
        "https://cdn.test/second.jpg",
        "https://cdn.test/third.jpg",
    ]


def test_candidates_exclude_resizers_and_junk():
    page = ExtractedMedia(
        image_url="https://assets-api.kathmandupost.com/thumb.php?src=x",
        image_candidates=[
            "https://assets-api.kathmandupost.com/thumb.php?src=x",
            "https://cdn.test/logo.png",
            "https://cdn.test/real.jpg",
        ],
    )
    assert candidate_images(None, page) == ["https://cdn.test/real.jpg"]


def test_candidates_tolerate_a_page_with_no_ranking_recorded():
    """ExtractedMedia built by hand (tests, older callers) has no list."""
    page = ExtractedMedia(image_url="https://cdn.test/og.jpg")
    assert candidate_images(None, page) == ["https://cdn.test/og.jpg"]


def test_strip_size_suffix_leaves_an_unsuffixed_url_alone():
    plain = "https://cdn.test/2026/10/story.jpg"
    assert strip_size_suffix(plain) == plain


def test_strip_size_suffix_removes_a_query_width():
    assert strip_size_suffix("https://cdn.test/a.jpg?w=800") == "https://cdn.test/a.jpg"


# --- end to end: what actually reaches the database ---

async def test_a_live_image_is_written_to_both_columns():
    """
    image_highres_url and image_url used to be allowed to disagree, and
    the card prefers the highres column. If they agree only because the
    highres one is proven live, the article page stops rendering a dead
    image in preference to the good one sitting next to it.
    """
    feed = {"image_url": None}
    page = ExtractedMedia(
        image_url="https://cdn.test/og.jpg",
        image_candidates=["https://cdn.test/og.jpg"],
    )
    media = merge_media(feed, page)

    async with httpx.AsyncClient(
        transport=transport({"/og.jpg": build()})
    ) as client:
        verified, dropped = await verify_media_images(
            media, ImageValidator(client), feed, page
        )

    assert dropped == 0
    assert verified["image_url"] == "https://cdn.test/og.jpg"
    assert verified["image_highres_url"] == "https://cdn.test/og.jpg"


async def test_a_dead_highres_does_not_sink_a_live_featured_image():
    """
    The highres column is a guess: upgrade_to_highres rewrites a known
    thumbnail into a sibling filename that frequently does not exist.
    The web card prefers it, so a dead guess beats a live image on the
    article page. Validation removes the guess instead of the artwork.
    """
    feed = {"image_url": "https://cdn.test/feed-320x180.jpg"}
    page = ExtractedMedia(
        image_url="https://cdn.test/dead-highres.jpg",
        image_highres_url="https://cdn.test/dead-highres.jpg",
        image_candidates=[
            "https://cdn.test/dead-highres.jpg",
            "https://cdn.test/live.jpg",
        ],
    )
    media = merge_media(feed, page)

    async with httpx.AsyncClient(
        transport=transport(
            {"/dead-highres.jpg": build(status=403), "/live.jpg": build()}
        )
    ) as client:
        verified, dropped = await verify_media_images(
            media, ImageValidator(client), feed, page
        )

    assert dropped == 0
    assert verified["image_url"] == "https://cdn.test/live.jpg"
    assert verified["image_highres_url"] == "https://cdn.test/live.jpg"


async def test_an_article_with_no_loadable_image_is_stored_with_no_artwork():
    """
    The honest outcome, and the whole point: no placeholder URL is
    invented. The web app already renders an imageless article cleanly
    and now sorts it below illustrated ones, so discarding the artwork
    is better than shipping a broken tile.
    """
    feed = {"image_url": "https://cdn.test/feed.jpg"}
    page = ExtractedMedia(
        image_url="https://cdn.test/og.jpg",
        image_alt="A photo of a bridge",
        image_candidates=["https://cdn.test/og.jpg"],
    )
    media = merge_media(feed, page)

    async with httpx.AsyncClient(
        transport=transport(
            {"/og.jpg": build(status=403), "/feed.jpg": build(status=404)}
        )
    ) as client:
        verified, dropped = await verify_media_images(
            media, ImageValidator(client), feed, page
        )

    assert dropped == 1
    assert verified["image_url"] is None
    assert verified["image_highres_url"] is None
    # The card renders alt text in place of the picture. Left attached
    # to an article with no image, that text stands in for artwork the
    # reader never got.
    assert verified["image_alt"] is None


async def test_a_dead_gif_url_is_dropped_rather_than_rendered():
    """
    gif_url is a real <img> candidate in the card, so an unvalidated one
    is exactly the broken tile this change exists to stop.
    """
    feed = {"image_url": "https://cdn.test/feed.jpg", "gif_url": "https://cdn.test/a.gif"}
    page = None
    media = merge_media(feed, page)

    async with httpx.AsyncClient(
        transport=transport(
            {
                "/feed.jpg": build(),
                "/a.gif": build(status=403, content_type="image/gif"),
            }
        )
    ) as client:
        verified, _ = await verify_media_images(
            media, ImageValidator(client), feed, page
        )

    assert verified["image_url"] == "https://cdn.test/feed.jpg"
    assert verified["gif_url"] is None


async def test_a_article_with_no_image_at_all_is_left_untouched():
    """No image proposed means no request and no dropped count."""
    async with httpx.AsyncClient(transport=transport({})) as client:
        validator = ImageValidator(client)
        media = {"image_url": None, "image_highres_url": None}
        verified, dropped = await verify_media_images(
            media, validator, {}, None
        )

    assert dropped == 0
    assert verified == media
    assert validator.requests == 0


# =============================================================
# Image URLs that are not images
#
# Two shapes reached the feed from Kathmandu Post: a base URL the
# scraper truncated at a directory (the CDN answers 403), and the
# AUTHOR avatar path, which loads fine and is the wrong picture. The
# second reads as a duplicate-photo bug; the first reads as a dead card
# no rendition fallback can rescue.
# =============================================================

def test_a_url_with_no_filename_is_junk():
    from app.sources.media import _looks_like_junk_image

    # A truncated path — there is no file behind it at any size.
    assert _looks_like_junk_image(
        "https://assets-cdn.kathmandupost.com/uploads/source/"
    )
    assert _looks_like_junk_image("https://example.com/media")
    assert _looks_like_junk_image("")


def test_an_author_avatar_is_not_the_article_image():
    from app.sources.media import _looks_like_junk_image

    # Loads 200, so no network probe would call it broken — but it is a
    # byline portrait, not the story's photograph.
    assert _looks_like_junk_image(
        "https://assets-cdn.kathmandupost.com/uploads/source/author/s/OVqEAY57400x400-1784694175.jpg"
    )


def test_real_images_are_not_caught_by_the_new_filters():
    from app.sources.media import _looks_like_junk_image

    for url in (
        "https://npcdn.ratopati.com/media/news/netanyu-and-trump_6s7Qyt02JI.jpg",
        "https://cdn.kathmandupost.com/pics/2026/10/story-1200x675.jpg",
        "https://example.com/a/b/photo.webp",
        "https://example.com/a/b/photo.jpg?v=2",
        "https://example.com/a/b/photo.avif",
    ):
        assert not _looks_like_junk_image(url), url

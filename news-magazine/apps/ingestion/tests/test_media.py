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
    _best_srcset_url,
    _chrome_spans,
    _clean_url,
    _inside_any,
    _is_banner_img,
    _is_furniture_img,
    _is_resizer,
    _looks_like_junk_image,
    _rank_image_candidates,
    _score_image,
    _size_tokens,
    best_image,
    candidate_images,
    extract_from_article,
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
    An outlet that sets og:image to its channel logo makes every story
    render the same picture, so a body photograph must still be able to
    beat a large declared image.

    The ranking now decides this by skipping a junk-shaped og:image
    outright rather than by out-scoring it - see
    test_metadata_that_is_plainly_furniture_is_skipped. The arithmetic
    below is kept because CONTENT_IMAGE_BONUS is still what separates two
    body images from each other.
    """
    og_score = _score_image("https://a.com/logo-1200x675.jpg", 1200, 675)
    body_score = _score_image("https://a.com/story.jpg", None, None) + CONTENT_IMAGE_BONUS
    assert body_score > og_score


# =============================================================
# Picking the RIGHT image, not merely a live one
#
# The bug these pin: a story about cooking meat for Dashain rendered a
# photograph of gold jewellery. The page had a correct og:image the whole
# time; it was out-scored by an <img> in the page's utility-icon strip,
# because WordPress writes the UPLOAD YEAR into the path and _SIZE_TOKEN_RE
# read that year as a pixel width. Every candidate also returned HTTP 200
# with an image content-type, so ImageValidator was right to accept it and
# still the wrong picture. Nothing in the pipeline asked whether the image
# was the story's.
#
# Every page shape below is taken from the real nepalnews.com article at
# /2026/10/20/be-careful-when-eating-meat-during-dashain-how-much-to-eat-
# and-how-to-cook-it/, which was fetched and read while fixing this.
# =============================================================

DASHAIN_URL = (
    "https://nepalnews.com/2026/10/20/be-careful-when-eating-meat-during-"
    "dashain-how-much-to-eat-and-how-to-cook-it/"
)

MEAT = "https://nepalnews.com/wp-content/uploads/2026/10/meat.jpg"
GOLD = "https://english.nepalnews.com/wp-content/uploads/2025/01/gold.png"


def _page(body_images: str, meta: str = "") -> str:
    """A minimal page: some metadata, then body images in reading order."""
    return f"<html><head>{meta}</head><body>{body_images}</body></html>"


async def _resolve(html: str, url: str = DASHAIN_URL) -> ExtractedMedia:
    """Run the real extractor over a page served from memory."""
    async def handler(request):
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        return await extract_from_article(url, client)


# ---- the headline case -------------------------------------------------

@pytest.mark.asyncio
async def test_a_correct_og_image_beats_a_logo_sitting_first_on_the_page():
    """
    The reported bug. The page declares the right picture in og:image and
    opens with a utility bar of icons — flight, weather, air quality,
    forex, gold rate — whose containers are named "utility-icons
    sidebar-utilities".

    The og:image must win outright. It is the outlet's own statement about
    this story; an icon in a navigation strip is not a candidate at all.
    """
    html = _page(
        '<div class="utility-icons sidebar-utilities">'
        f'<a href="/gold-silver"><img src="{GOLD}" /></a>'
        "</div>"
        f'<img src="{MEAT}" alt="" />',
        f'<meta property="og:image" content="{MEAT}" />',
    )
    result = await _resolve(html)

    assert result.image_url == MEAT
    assert result.provenance["image_url"] == "og:image"


@pytest.mark.asyncio
async def test_the_gold_icon_is_never_chosen_even_when_it_is_the_first_image():
    """
    The same page with the og:image removed, so the extractor has to fall
    back to scanning. It must still reach the photograph.

    The icon has no class, no alt text and no dimensions to judge it by,
    and it returns HTTP 200 with image/png, so neither the junk filter nor
    the liveness probe will catch it. The only thing standing between it
    and the article's artwork is the container it sits in.
    """
    html = _page(
        '<div class="utility-icons sidebar-utilities">'
        f'<a href="/gold-silver"><img src="{GOLD}" /></a>'
        "</div>"
        f'<img src="{MEAT}" alt="" />',
    )
    result = await _resolve(html)

    assert result.image_url == MEAT
    assert GOLD not in result.image_candidates


@pytest.mark.asyncio
async def test_a_utility_bar_not_named_sidebar_is_still_furniture():
    """
    Themes rename their classes. "utility-icons" with no "sidebar" in it
    must be recognised just the same, or a single rename silently starts
    putting gold icons on articles.
    """
    html = _page(
        '<div class="utility-icons d-flex">'
        f'<a href="/gold-silver"><img src="{GOLD}" /></a>'
        "</div>"
        f'<img src="{MEAT}" alt="" />',
    )
    result = await _resolve(html)

    assert result.image_url == MEAT


# ---- the year that was being read as a width --------------------------

@pytest.mark.asyncio
async def test_the_upload_year_in_the_path_does_not_decide_the_winner():
    """
    WordPress never moves a re-used photograph, so an article can carry an
    image from /uploads/2023/ while the furniture around it was uploaded
    this year. Scoring the path year as a pixel width handed the article to
    the newest picture on the page.

    The story's own photograph must win regardless of which year its file
    happens to live in.
    """
    for year in ("2026", "2024", "2021"):
        old_photo = f"https://nepalnews.com/wp-content/uploads/{year}/07/meat.jpg"
        html = _page(
            f'<img src="{old_photo}" alt="" />'
            '<div class="section-padding"><div class="row row-cols-4 g-4">'
            '<div class="col"><div class="card"><a href='
            '"https://nepalnews.com/2026/10/20/teenager-dies-in-motorcycle-accident/">'
            '<div class="image-container">'
            '<img src="https://nepalnews.com/wp-content/uploads/2026/09/accident.jpeg" />'
            "</a></div></div></div></div></div>",
        )
        result = await _resolve(html)
        assert result.image_url == old_photo, year


def test_a_bare_year_in_an_upload_path_is_not_read_as_a_pixel_width():
    """The scoring bug itself, isolated from the layout around it."""
    assert _size_tokens("https://nepalnews.com/wp-content/uploads/2026/10/meat.jpg") == []
    assert _size_tokens("https://nepalnews.com/wp-content/uploads/2019/04/x.jpg") == []


def test_a_real_size_token_is_still_read():
    """Rendition sizes must keep working, or the module stops picking HD."""
    assert _size_tokens("https://a.com/photo-1200x675.jpg") == [1200]
    assert _size_tokens("https://a.com/photo_320x180.jpg") == [320]
    assert _size_tokens("https://a.com/w1200/photo.jpg") == [1200]
    assert _size_tokens("https://a.com/width_800/photo.jpg") == [800]


def test_a_related_thumbnail_size_token_cannot_outrank_the_photo():
    """
    In reading order the story's photograph comes first, so it wins. A
    later image ending -1024x556-1.jpg used to jump the queue on its size
    token alone.
    """
    html = _page(
        f'<img src="{MEAT}" alt="" />'
        '<img src="https://nepalnews.com/wp-content/uploads/2026/02/drone-1024x556-1.jpg" />'
    )
    ranked = _rank_image_candidates(html, {}, "https://nepalnews.com/story/")
    assert ranked[0][2] == MEAT


def test_a_year_bearing_url_does_not_outscore_a_real_rendition():
    """A 2026 upload is not a 2026-pixel-wide image."""
    dated = "https://nepalnews.com/wp-content/uploads/2026/10/meat.jpg"
    sized = "https://a.com/photo-1200x675.jpg"
    assert _score_image(dated, None, None) < _score_image(sized, None, None)


# ---- byline portraits and ad strips ------------------------------------

@pytest.mark.asyncio
async def test_a_byline_portrait_is_not_the_article_image():
    """
    The one piece of furniture that sits inside the article column, ahead
    of the photograph. nepalnews.com serves it from
    .../uploads/2025/08/Nepalnews.jpg — an ordinary filename with no junk
    token in it — and tags it class="post-author-image".

    The URL gives nothing away, so the tag's own class has to.
    """
    html = _page(
        '<div class="author-writer writers-box d-flex">'
        '<div class="author-images-stack">'
        '<a href="https://nepalnews.com/author/nepalnews/">'
        '<img src="https://nepalnews.com/wp-content/uploads/2025/08/Nepalnews.jpg"'
        ' alt="नेपलन्युज" class="post-author-image" />'
        "</a></div></div>"
        f'<img src="{MEAT}" alt="" />',
    )
    result = await _resolve(html)

    assert result.image_url == MEAT


@pytest.mark.asyncio
async def test_a_leaderboard_ad_strip_is_not_the_article_image():
    """
    <img width="1200" height="110" src=".../1200-X-110.gif"> sits directly
    above the article. Ten-to-one is an advertising slot, never a news
    photograph.
    """
    html = _page(
        '<img width="1200" height="110"'
        ' src="https://nepalnews.com/wp-content/uploads/2026/09/1200-X-110.gif"'
        ' class="image wp-image-212594" alt="" />'
        f'<img src="{MEAT}" alt="" />',
    )
    result = await _resolve(html)

    assert result.image_url == MEAT


def test_a_wide_short_img_is_recognised_as_a_banner():
    assert _is_banner_img({"width": "1200", "height": "110"})
    assert _is_banner_img({"width": "970", "height": "90"})
    # A real photograph is never a strip.
    assert not _is_banner_img({"width": "1200", "height": "675"})
    assert not _is_banner_img({"width": "800", "height": "600"})
    # No declared dimensions means no opinion.
    assert not _is_banner_img({})


def test_a_byline_class_marks_an_img_as_furniture():
    assert _is_furniture_img('<img src="https://a.com/x.jpg" class="post-author-image">')
    assert _is_furniture_img('<img src="https://a.com/x.jpg" class="author-avatar round">')
    assert _is_furniture_img('<img src="https://a.com/x.jpg" alt="writer portrait">')


def test_a_photo_class_is_not_mistaken_for_furniture():
    """
    Only byline/identity words disqualify. "featured", "main" and "large"
    describe real photographs, and rejecting them would throw away the
    article's own image.
    """
    for tag in (
        '<img src="https://a.com/x.jpg" class="article-featured-image">',
        '<img src="https://a.com/x.jpg" class="main-photo large">',
        '<img src="https://a.com/x.jpg" class="wp-image-212594 attachment-full">',
        '<img src="https://a.com/x.jpg" alt="ministers cut a ribbon">',
    ):
        assert not _is_furniture_img(tag), tag


# ---- metadata outranks page furniture, always --------------------------

@pytest.mark.asyncio
async def test_metadata_is_preferred_over_a_body_image():
    """
    The rule the fix rests on: the outlet's own og:image is a statement
    about this article, and anything found by scanning the page is a guess
    about its layout.
    """
    html = _page(
        '<div class="related-stories"><img src="https://a.com/other-story.jpg" /></div>'
        '<img src="https://a.com/in-body-photo.jpg" alt="" />',
        '<meta property="og:image" content="https://a.com/declared.jpg" />',
    )
    result = await _resolve(html)

    assert result.image_url == "https://a.com/declared.jpg"
    assert result.provenance["image_url"] == "og:image"


@pytest.mark.asyncio
async def test_metadata_that_is_plainly_furniture_is_skipped():
    """
    The Ministry of Foreign Affairs set og:image to their channel logo, so
    every story from them rendered the same picture. A metadata URL that
    says it is a logo is not a candidate.
    """
    html = _page(
        '<img src="https://mofa.gov.np/wp-content/uploads/2019/04/summit.jpg" alt="" />',
        '<meta property="og:image" content="https://mofa.gov.np/img/logo.png" />'
        '<meta property="og:image:width" content="1200" />'
        '<meta property="og:image:height" content="675" />',
    )
    result = await _resolve(html)

    assert result.image_url == "https://mofa.gov.np/wp-content/uploads/2019/04/summit.jpg"
    assert result.provenance["image_url"] == "body"


@pytest.mark.asyncio
async def test_twitter_image_is_used_when_there_is_no_og_image():
    html = _page(
        '<img src="https://a.com/in-body.jpg" alt="" />',
        '<meta name="twitter:image" content="https://a.com/twitter.jpg" />',
    )
    result = await _resolve(html)

    assert result.image_url == "https://a.com/twitter.jpg"
    assert result.provenance["image_url"] == "twitter:image"


@pytest.mark.asyncio
async def test_og_image_survives_when_the_page_has_no_images_at_all():
    """Metadata is the whole answer when there is nothing else to find."""
    html = _page("", '<meta property="og:image" content="https://a.com/only.jpg" />')
    result = await _resolve(html)

    assert result.image_url == "https://a.com/only.jpg"


# ---- srcset ------------------------------------------------------------

def test_the_largest_srcset_rendition_is_chosen():
    """
    WordPress emits srcsets smallest first, so taking the first entry
    stored the rendition a phone would get instead of the largest offered.
    """
    srcset = (
        "https://a.com/p-320.jpg 320w, "
        "https://a.com/p-1200.jpg 1200w, "
        "https://a.com/p-800.jpg 800w"
    )
    assert _best_srcset_url(srcset) == "https://a.com/p-1200.jpg"


def test_srcset_density_descriptors_are_honoured():
    srcset = "https://a.com/p.jpg 1x, https://a.com/p@2x.jpg 2x"
    assert _best_srcset_url(srcset) == "https://a.com/p@2x.jpg"


def test_an_unparseable_srcset_falls_back_to_the_first_entry():
    """Never worse than what _clean_url would have chosen."""
    assert _best_srcset_url("https://a.com/p.jpg") == "https://a.com/p.jpg"


@pytest.mark.asyncio
async def test_a_srcset_image_stores_its_largest_rendition():
    html = _page(
        '<img srcset="https://a.com/photo-320.jpg 320w, '
        'https://a.com/photo-1200.jpg 1200w" src="https://a.com/photo-320.jpg" alt="" />'
    )
    result = await _resolve(html, "https://a.com/story/")

    assert result.image_url == "https://a.com/photo-1200.jpg"


# ---- the ranking keeps offering alternates -----------------------------

@pytest.mark.asyncio
async def test_the_correct_image_is_first_and_alternates_are_kept():
    """
    resolve_live_image walks this list and takes the first that loads, so
    the correct image has to lead AND the page's other candidates have to
    survive behind it. A CDN that 403s the winner must not cost the
    article its artwork.
    """
    html = _page(
        '<img src="https://nepalnews.com/wp-content/uploads/2019/04/summit.jpg" alt="" />'
        '<div class="related-stories">'
        '<img src="https://nepalnews.com/wp-content/uploads/2026/09/accident.jpeg" />'
        "</div>",
        f'<meta property="og:image" content="{MEAT}" />',
    )
    result = await _resolve(html)

    assert result.image_candidates[0] == MEAT
    assert len(result.image_candidates) > 1


@pytest.mark.asyncio
async def test_an_image_repeated_on_the_page_is_stored_once():
    """
    The outlet's og:image is usually the same file the body renders, and
    the two must collapse to one candidate rather than occupying two
    ranks of the same list.
    """
    html = _page(
        f'<img src="{MEAT}" alt="" />',
        f'<meta property="og:image" content="{MEAT}" />'
        f'<meta name="twitter:image" content="{MEAT}" />',
    )
    result = await _resolve(html)

    assert result.image_candidates == [MEAT]


@pytest.mark.asyncio
async def test_the_wrong_picture_is_not_merely_ranked_lower_but_removed():
    """
    A related-story thumbnail must not sit in the candidate list at all
    when the outlet has already told us what the story looks like. Ranked
    lower it would still be stored whenever the winner 403s.
    """
    html = _page(
        f'<img src="{MEAT}" alt="" />'
        '<div class="section-padding"><div class="row row-cols-4 g-4">'
        '<div class="col"><div class="card h-100">'
        '<a href="https://nepalnews.com/2026/10/20/teenager-dies-in-motorcycle-accident/">'
        '<div class="image-container">'
        '<img src="https://nepalnews.com/wp-content/uploads/2026/09/accident.jpeg" />'
        "</a></div></div></div></div></div>",
        f'<meta property="og:image" content="{MEAT}" />',
    )
    result = await _resolve(html)

    assert result.image_url == MEAT
    assert all("accident.jpeg" not in url for url in result.image_candidates)


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

    The page here carries its own photograph, so that photograph is
    confirmed and survives; the feed's picture and the dead gif go.
    """
    feed = {"image_url": "https://cdn.test/feed.jpg", "gif_url": "https://cdn.test/a.gif"}
    page = ExtractedMedia(
        image_url="https://cdn.test/og.jpg",
        image_candidates=["https://cdn.test/og.jpg"],
    )
    media = merge_media(feed, page)

    async with httpx.AsyncClient(
        transport=transport(
            {
                "/feed.jpg": build(),
                "/og.jpg": build(),
                "/a.gif": build(status=403, content_type="image/gif"),
            }
        )
    ) as client:
        verified, _ = await verify_media_images(
            media, ImageValidator(client), feed, page
        )

    assert verified["image_url"] == "https://cdn.test/og.jpg"
    assert verified["gif_url"] is None


async def test_a_feed_picture_with_no_page_is_not_stored():
    """
    The feed's picture is the publisher's choice about a slot in a
    feed, and it is regularly a promoted or related story's photograph
    - the "Most Read from World" thumbnail that Kathmandu Post puts
    on every one of its article pages, which is how a story about PHP
    security was illustrated with an elephant.

    So a picture that was not confirmed against the story's OWN page
    is not stored at all. The story is stored bare, and the card shows
    the story's own key points.
    """
    feed = {"image_url": "https://cdn.test/feed.jpg"}
    page = None
    media = merge_media(feed, page)

    async with httpx.AsyncClient(
        transport=transport({"/feed.jpg": build()})
    ) as client:
        verified, dropped = await verify_media_images(
            media, ImageValidator(client), feed, page
        )

    assert verified["image_url"] is None
    assert verified["image_highres_url"] is None
    assert verified["image_verified"] is False
    assert dropped == 1


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


# =============================================================
# Repairing a row that was stored without artwork
#
# The bug this covers: the poll's per-article pipeline runs media
# enrichment ONCE, on the poll that first stores an article. Every later
# sighting hits dedup.check, matches the stored row on content_hash or
# canonical_url, and continues before the media block ever runs. So a
# single transient probe failure — a 429, a 403 from a CDN that
# answered 200 a minute earlier, a timed-out connection — left the row
# permanently imageless, and the web renders that as "NO IMAGE
# AVAILABLE". Checked against the live feed: every one of the seven
# imageless articles in the newest sixty rows had a live og:image on
# its own page. The picture was always there; nothing went back for it.
#
# repair_missing_images is that going back.
# =============================================================

# The source whose poll is calling the repair pass.
SOURCE_ID = "22222222-2222-2222-2222-222222222222"

ARTICLE_PAGE = (
    "<html><head>"
    '<meta property="og:image" content="https://cdn.test/recovered.jpg" />'
    "<meta property='og:title' content='A story' />"
    f"<body><p>{'Real prose about the story. ' * 8}</p></body></html>"
)


class FakeQuery:
    """
    The slice of supabase-postgrest's builder these tests drive.

    Records every `.update()` so a test can assert on the columns the
    repair pass actually wrote, which is the whole point of the pass.
    """

    def __init__(self, rows, updates, log):
        self._rows = rows
        self._updates = updates
        self._log = log

    def select(self, *a, **k):
        return self

    def eq(self, column, value=None):
        self._log.append(("eq", column, value))
        return self

    def is_(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._log.append(("limit", n))
        return self

    def execute(self):
        return type("Response", (), {"data": self._rows})()

    def update(self, values):
        self._updates.append(values)
        return self


class FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []
        self.log = []

    def table(self, name):
        return FakeQuery(self._rows, self.updates, self.log)


ROWS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "slug": "a-story",
        "headline": "A story",
        "source_article_url": "https://site.test/a-story",
        "canonical_url": None,
    }
]


async def test_a_stored_row_with_no_image_is_given_one_on_the_next_pass():
    """
    The repair itself. The row is stored and imageless; the article page
    now offers an og:image and it loads; the pass must write both image
    columns.
    """
    from app.services.ingestion import repair_missing_images

    db = FakeDb(ROWS)
    routes = {
        "/a-story": build(content_type="text/html", body=ARTICLE_PAGE.encode()),
        "/recovered.jpg": build(),
    }

    async with httpx.AsyncClient(transport=transport(routes)) as client:
        repaired = await repair_missing_images(
            db, SOURCE_ID, client, ImageValidator(client), limit=5
        )

    assert repaired == 1
    assert len(db.updates) == 1
    assert db.updates[0]["featured_image_url"] == "https://cdn.test/recovered.jpg"
    # Both columns, not just featured: the card reads image_highres_url
    # first, so a row repaired into only one column would still render
    # from whichever was left.
    assert db.updates[0]["image_highres_url"] == "https://cdn.test/recovered.jpg"


async def test_repair_leaves_a_row_alone_when_the_image_is_still_dead():
    """
    The pass must not write a URL it could not prove. An outage that
    lasted longer than one poll leaves the row as it was, and the
    read-side filter keeps it out of the feeds.
    """
    from app.services.ingestion import repair_missing_images

    db = FakeDb(ROWS)
    routes = {
        "/a-story": build(content_type="text/html", body=ARTICLE_PAGE.encode()),
        "/recovered.jpg": build(status=403, content_type="text/html", body=b"<html>"),
    }

    async with httpx.AsyncClient(transport=transport(routes)) as client:
        repaired = await repair_missing_images(
            db, SOURCE_ID, client, ImageValidator(client), limit=5
        )

    assert repaired == 0
    assert db.updates == []


async def test_repair_does_nothing_when_there_is_nothing_to_repair():
    """The overwhelmingly common case: no imageless rows, no requests."""
    from app.services.ingestion import repair_missing_images

    db = FakeDb([])
    seen: list = []

    async with httpx.AsyncClient(transport=transport({}, seen)) as client:
        repaired = await repair_missing_images(
            db, SOURCE_ID, client, ImageValidator(client), limit=5
        )

    assert repaired == 0
    assert seen == []


async def test_repair_is_bounded_by_the_limit_it_is_given():
    """
    This runs on the critical path of every source poll, so it must not
    become a crawl of the outlet.
    """
    from app.services.ingestion import repair_missing_images

    db = FakeDb(ROWS)

    async with httpx.AsyncClient(transport=transport({})) as client:
        await repair_missing_images(
            db, SOURCE_ID, client, ImageValidator(client), limit=2
        )

    assert ("limit", 2) in db.log


async def test_repair_survives_a_database_failure():
    """
    A sweep that cannot read its candidates must report zero and let the
    poll carry on. The rows it would have fixed are still hidden from the
    feeds by the read-side pass, so failing loudly buys nothing.
    """
    from app.services.ingestion import repair_missing_images

    class BrokenDb:
        def table(self, name):
            raise RuntimeError("connection reset")

    async with httpx.AsyncClient(transport=transport({})) as client:
        repaired = await repair_missing_images(
            BrokenDb(), SOURCE_ID, client, ImageValidator(client), limit=5
        )

    assert repaired == 0


async def test_repair_only_ever_looks_at_its_own_sources_rows():
    """
    With ~27 sources polling independently, a global scan would have every
    one of them retry the same handful of rows — the opposite of bounded,
    and the wrong unit anyway, since a row's artwork is that outlet's own
    CDN. Scoped, the work spreads across the pollers.
    """
    from app.services.ingestion import repair_missing_images

    db = FakeDb(ROWS)

    async with httpx.AsyncClient(transport=transport({})) as client:
        await repair_missing_images(
            db, SOURCE_ID, client, ImageValidator(client), limit=5
        )

    assert ("eq", "source_id", SOURCE_ID) in db.log

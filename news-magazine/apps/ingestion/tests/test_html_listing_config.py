"""
The html_listing adapter's selector_config handling.

TWO BUGS, BOTH FOUND BY VERIFYING OUTLETS AGAINST THE LIVE WEB
--------------------------------------------------------------

1. `title` was never assigned when a selector_config named an
   `item` but no `title`. Every seeded outlet in migration 0008 is
   written in exactly that shape:

       {"item":"article, .post-item, .news-item","link":"a[href]", ...}

   so the first card whose `item` selector actually matched raised
   UnboundLocalError. The listing then failed with a stack trace
   that read like a broken website rather than a broken config.

2. A card could not BE the link. BeautifulSoup's select_one() only
   searches descendants, so an `item` selector that picked the
   anchor itself found no link inside itself, every card was
   dropped, and the adapter silently fell back to its anchor
   heuristic -- ignoring the operator's selector entirely and
   reporting success while doing something else.

   That second one is why the seeded selectors, which are written
   in the anchor form ("a[href^=\"/nepal/\"]"), appeared to work:
   they never actually ran.

WHAT IS ASSERTED HERE
---------------------
That a config naming an `item` and no `title` parses instead of
crashing, that an anchor can be its own card, that a container card
still works, and that the operator's selector is actually obeyed
rather than quietly replaced by the heuristic.
"""

from __future__ import annotations

from app.sources.html_listing import HtmlListingAdapter


def adapter(selector_config: dict, name: str = "Test") -> HtmlListingAdapter:
    return HtmlListingAdapter({
        "name": name,
        "base_url": "https://example.invalid/",
        "fallback_url": "https://example.invalid/",
        "allowed": True,
        "rate_limit_per_minute": 3,
        "default_language": "ne",
        "selector_config": selector_config,
    })


# ---------------------------------------------------------------------------
# Bug 1: a config with no `title` key
# ---------------------------------------------------------------------------

def test_a_config_with_no_title_key_does_not_crash():
    """
    The seeded shape. This used to raise UnboundLocalError on the
    first card.
    """
    html = """
    <html><body>
      <div class="post-item">
        <a href="/news/first-story">काठमाडौंमा नयाँ पुल निर्माण कार्य सुरु भयो</a>
      </div>
      <div class="post-item">
        <a href="/news/second-story">धनगढीमा पनौतीको भेल बढ्दो, तीन दिन अघि नै खुल्यो</a>
      </div>
    </body></html>
    """

    entries = adapter({
        "item": ".post-item",
        "link": "a[href]",
        "exclude": r"/author|/category/",
    }).parse(html)

    assert len(entries) == 2
    assert entries[0]["title"] == "काठमाडौंमा नयाँ पुल निर्माण कार्य सुरु भयो"


def test_a_config_with_no_title_key_still_honours_the_exclude():
    html = """
    <html><body>
      <div class="post-item"><a href="/author/someone">x</a></div>
      <div class="post-item"><a href="/news/real-story">देशभरका वडामा मतदान सुरु भयो अहिले</a></div>
    </body></html>
    """

    entries = adapter({
        "item": ".post-item",
        "link": "a[href]",
        "exclude": r"/author",
    }).parse(html)

    assert len(entries) == 1
    assert "/news/real-story" in entries[0]["link"]


# ---------------------------------------------------------------------------
# Bug 2: the card is the link
# ---------------------------------------------------------------------------

def test_an_anchor_can_be_its_own_card():
    """
    The `item` selector picks the anchor, and that anchor IS the
    link. This is the shape most modern card markup uses and the
    shape the seeded selectors are written in.
    """
    html = """
    <html><body>
      <a href="/nepal/some-headline-here">Some headline here</a>
      <a href="/nepal/other-headline-here">Second story says something worth reading here</a>
      <a href="/about-us/contact-the-office">About the office</a>
    </body></html>
    """

    entries = adapter({
        "item": "a[href*='/nepal/']",
        "link": "a[href]",
        "exclude": r"/about-us",
    }).parse(html)

    assert len(entries) == 2
    assert entries[0]["title"] == "Some headline here"
    assert entries[0]["link"] == "https://example.invalid/nepal/some-headline-here"


def test_an_anchor_card_carries_its_own_image():
    html = """
    <html><body>
      <a href="/nepal/a-headline-here">
        <img src="https://example.invalid/photo.jpg" alt="a photo">
        <h3>The headline of the story here</h3>
      </a>
    </body></html>
    """

    entries = adapter({
        "item": "a[href*='/nepal/']",
        "link": "a[href]",
    }).parse(html)

    assert len(entries) == 1
    assert entries[0]["image"] == "https://example.invalid/photo.jpg"


def test_an_anchor_card_falls_back_to_the_title_attribute():
    """
    Some outlets put the headline in the anchor's title attribute and
    leave the visible text empty, because the anchor wraps only the
    picture.
    """
    html = """
    <html><body>
      <a href="/nepal/a-headline-here" title="The headline of the story here">
        <img src="https://example.invalid/photo.jpg" alt="">
      </a>
    </body></html>
    """

    entries = adapter({
        "item": "a[href*='/nepal/']",
        "link": "a[href]",
    }).parse(html)

    assert len(entries) == 1
    assert entries[0]["title"] == "The headline of the story here"


# ---------------------------------------------------------------------------
# The operator's selector is obeyed
# ---------------------------------------------------------------------------

def test_the_item_selector_is_actually_used():
    """
    Before the fix, an `item` selector that matched nothing fell
    through to the anchor heuristic and quietly produced a DIFFERENT
    list. The operator's selector is now the one that decides.
    """
    html = """
    <html><body>
      <a href="/wanted/one-headline-here">The one the operator asked for here</a>
      <a href="/unwanted/two-headlines-here">The one the operator did not want</a>
    </body></html>
    """

    entries = adapter({
        "item": "a[href*='/wanted/']",
        "link": "a[href]",
    }).parse(html)

    assert len(entries) == 1
    assert "/wanted/one-headline-here" in entries[0]["link"]


def test_a_container_card_still_works():
    """
    The other shape: a container that HOLDS the link.
    """
    html = """
    <html><body>
      <article class="card">
        <h2><a href="/news/a-headline-here">Nepal to host the world cricket qualifier next month</a></h2>
      </article>
    </body></html>
    """

    entries = adapter({
        "item": "article.card",
        "link": "h2 a",
        "title": "h2 a",
    }).parse(html)

    assert len(entries) == 1
    assert entries[0]["title"] == "Nepal to host the world cricket qualifier next month"
    assert entries[0]["link"] == "https://example.invalid/news/a-headline-here"


def test_a_title_selector_is_still_honoured():
    html = """
    <html><body>
      <article class="card">
        <a href="/news/a-headline-here"><img src="https://example.invalid/p.jpg"></a>
        <h2 class="headline">The real headline of this story</h2>
      </article>
    </body></html>
    """

    entries = adapter({
        "item": "article.card",
        "link": "a[href]",
        "title": ".headline",
    }).parse(html)

    assert len(entries) == 1
    assert entries[0]["title"] == "The real headline of this story"

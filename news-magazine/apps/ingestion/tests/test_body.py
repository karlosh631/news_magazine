"""
Tests for the full article body producer.

These pin the bug behind "read more expands the excerpt and stops":

`sources.republish_permission` is the operator's per-outlet licence to
republish a publisher's body, and app/sources/base.py has enforced it
from the start by nulling `full_body_html`. But for as long as that
gate existed, nothing ever POPULATED the field — both adapters
hard-set `full_body_html=None` at normalize() time, which is before
any page has been fetched, and nothing downstream read it either. The
gate was guarding a value nobody produced, so flipping the flag in the
database changed nothing.

The three properties that matter, and that these tests hold in place:

  1. Licensed -> the body is populated, as PLAIN TEXT.
  2. NOT licensed -> the body stays None even when a page was already
     fetched and sitting in hand. This is the licence guarantee and it
     must fail closed.
  3. The setting is off -> no page fetch happens at all. Body storage
     must not quietly become a per-story crawl into 27 publishers.

Every request below is served by httpx.MockTransport. No test in this
file reaches the network.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.ingestion import _build_body
from app.sources.body import (
    MAX_BODY_LENGTH,
    MIN_BODY_CHARS,
    attach_full_body,
    body_text_from_extracted,
    permits_full_text,
    to_plain_paragraphs,
)
from app.sources.media import ExtractedMedia


# =============================================================
# Fakes
# =============================================================

def settings(enrich_body=True, fetch_full_text=False, user_agent="TestBot/1.0"):
    """
    A stand-in for app.config.Settings.

    A SimpleNamespace rather than a real Settings, because Settings
    requires SUPABASE_URL and two service keys at construction — the
    body producer reads three booleans and must not need credentials.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        enrich_body=enrich_body,
        fetch_full_text=fetch_full_text,
        default_user_agent=user_agent,
        default_request_timeout_seconds=15.0,
    )


def article(url="https://outlet.test/story/1", headline="A story", metadata=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        source_article_url=url,
        headline=headline,
        raw_metadata=metadata or {},
        full_body_html=None,
    )


LICENSED = {"id": "s1", "name": "Outlet", "republish_permission": True}
UNLICENSED = {"id": "s2", "name": "Outlet", "republish_permission": False}


def page_text(*paragraphs: str) -> str:
    return "\n\n".join(paragraphs)


LONG_PAGE = page_text(
    "The cabinet on Tuesday approved a revised schedule for the "
    "constitutional amendment that had been pending since the previous "
    "session, according to officials briefed on the decision.",
    "A second committee will review the text before it reaches the floor, "
    "and both the ruling and the opposition benches have asked for the "
    "scheduled time to be brought forward before the recess begins.",
)


def transport(routes: dict, seen: list | None = None) -> httpx.MockTransport:
    """
    Route by path; anything unrouted 404s. Records every request in
    `seen` so a test can assert that NO request was made at all.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, text="not found")
        return route(request)

    return httpx.MockTransport(handler)


def article_page(body: str):
    def make(request: httpx.Request | None = None) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=(
                "<html><head><title>A story</title></head><body>"
                f"<article class='story-content'><p>{body}</p></article>"
                "</body></html>"
            ),
        )

    return make


def run(coro):
    # asyncio.run(), not get_event_loop() — see test_store.py for why.
    return asyncio.run(coro)


class FakeRobots:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.checked: list[str] = []

    async def is_allowed(self, url: str, user_agent: str) -> bool:
        self.checked.append(url)
        return self.allowed


class FakeRateLimiter:
    def __init__(self):
        self.waits = 0

    async def wait(self) -> None:
        self.waits += 1


# =============================================================
# The licence
# =============================================================

def test_licensed_source_populates_the_body_from_the_already_fetched_page():
    """
    The headline bug. A source that grants republish_permission, with
    the article page ALREADY fetched for media enrichment, must end up
    with a body — otherwise the gate in base.py guards a value nothing
    produces and "read more" expands the excerpt forever.
    """
    subject = article()
    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True),
            extracted=ExtractedMedia(article_text=LONG_PAGE),
        )
    )

    assert stored is True
    assert subject.full_body_html
    assert "cabinet on Tuesday approved" in subject.full_body_html
    assert "A second committee will review" in subject.full_body_html


def test_body_is_plain_text_and_never_the_publishers_markup():
    """
    Full-text republication is a licensing decision; carrying a
    publisher's HTML to a reader is a security one, and they are
    separable. Storing the markup would put untrusted HTML in the
    database for no benefit — _build_body() rebuilds escaped paragraphs
    from text anyway.
    """
    subject = article()
    run(
        attach_full_body(
            subject,
            LICENSED,
            settings(),
            extracted=ExtractedMedia(
                article_text=(
                    "<div class='story'><p>Real first paragraph here, "
                    "carrying the actual words the outlet published "
                    "instead of the markup that wrapped them on the "
                    "page, and long enough to clear the stub floor.</p>"
                    "<script>alert(1)</script></div>"
                )
            ),
        )
    )

    body = subject.full_body_html or ""
    assert "alert(1)" not in body
    assert "<script" not in body
    assert "<div" not in body
    assert "Real first paragraph here" in body


def test_permission_denied_leaves_the_body_none_even_with_the_page_in_hand():
    """
    THE LICENCE GUARANTEE, and the test that matters most.

    The page was fetched — it is right there in `extracted`, already
    paid for, already parsed. That is precisely the situation in which a
    convenient implementation stores the text anyway. It must not.
    sources.republish_permission is the operator's decision about
    whether this publisher's words may be republished, and having the
    page is irrelevant to it. Fails closed.
    """
    subject = article()
    seen: list[httpx.Request] = []

    stored = run(
        attach_full_body(
            subject,
            UNLICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            extracted=ExtractedMedia(article_text=LONG_PAGE),
            client=httpx.AsyncClient(transport=transport({}, seen)),
        )
    )

    assert stored is False
    assert subject.full_body_html is None
    # And nothing was even asked of the network.
    assert seen == []


def test_permission_denied_makes_no_page_fetch_even_when_one_is_licensed_for():
    """
    Same guarantee, from the other side: with the licence refused there
    is no reason to spend a request finding out, so the own-fetch path
    must not run. A source with the flag false must cost exactly what
    it cost before this existed.
    """
    subject = article(url="https://outlet.test/story/9")
    seen: list[httpx.Request] = []

    stored = run(
        attach_full_body(
            subject,
            UNLICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            client=httpx.AsyncClient(transport=transport(
                {"/story/9": article_page(LONG_PAGE)}, seen
            )),
        )
    )

    assert stored is False
    assert subject.full_body_html is None
    assert seen == []


def test_a_source_row_missing_the_key_is_read_as_not_licensed():
    """
    A source row that lost the column, or a caller that passed the
    wrong dict, must read as "not licensed". Defaulting a licence flag
    to True would make every such mistake a republication.
    """
    assert permits_full_text({}) is False
    assert permits_full_text({"republish_permission": None}) is False
    assert permits_full_text({"republish_permission": False}) is False
    assert permits_full_text(None) is False
    assert permits_full_text({"republish_permission": True}) is True


def test_the_extractor_still_nulls_the_body_for_an_unlicensed_source():
    """
    base.py's own gate, unchanged. It runs before this module is ever
    reached, so it must keep working on its own — an adapter that
    somehow arrives carrying a body still loses it.
    """
    from app.sources.base import NormalizedArticle

    class Adapter:
        def __init__(self, row):
            self.source = row

        # The gate verbatim, lifted from SourceAdapter.validate().
        def validate(self, articles):
            out = []
            for a in articles:
                if not self.source.get("republish_permission", False):
                    a.full_body_html = None
                out.append(a)
            return out

    def leaked():
        # A fresh article each time: validate() mutates in place, so a
        # shared instance would be nulled by the first assertion and
        # the second would prove nothing.
        return NormalizedArticle(
            source_article_url="https://outlet.test/story/2",
            canonical_url="https://outlet.test/story/2",
            headline="Leaked body that an adapter bug carried in",
            published_at=None,
            full_body_html=LONG_PAGE,
        )

    assert Adapter(UNLICENSED).validate([leaked()])[0].full_body_html is None
    assert Adapter(LICENSED).validate([leaked()])[0].full_body_html == LONG_PAGE


# =============================================================
# The cost: no page fetch unless explicitly asked for
# =============================================================

def test_no_page_fetch_happens_when_the_setting_is_off():
    """
    Body storage is opt-in. With enrich_body false the function must not
    touch the article AND must not touch the network — even with the
    licence granted and even with fetch_full_text also on. Turning on
    the request knob must not turn on the feature by itself.
    """
    subject = article(url="https://outlet.test/story/3")
    seen: list[httpx.Request] = []

    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=False, fetch_full_text=True),
            client=httpx.AsyncClient(transport=transport(
                {"/story/3": article_page(LONG_PAGE)}, seen
            )),
        )
    )

    assert stored is False
    assert subject.full_body_html is None
    assert seen == [], "enrich_body=false must not produce a single request"


def test_no_page_fetch_happens_for_a_page_that_was_not_already_being_fetched():
    """
    The polite default, and the reason this feature cannot get the
    crawler blocked by accident.

    Most feed-only paths never open the article page at all: the feed
    carried an image, so media enrichment had no reason to fetch. Cover
    those stories means one request per story into 27 publishers. That
    is a crawl, not a poll, so it takes its own opt-in
    (fetch_full_text) rather than riding along on enrich_body.
    """
    subject = article(url="https://outlet.test/story/4")
    seen: list[httpx.Request] = []

    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True, fetch_full_text=False),
            client=httpx.AsyncClient(transport=transport(
                {"/story/4": article_page(LONG_PAGE)}, seen
            )),
        )
    )

    assert stored is False
    assert subject.full_body_html is None
    assert seen == []


def test_the_reused_page_costs_no_request_of_its_own():
    """
    The zero-extra-request property, pinned. Passing the ExtractedMedia
    the media pass already produced must reuse it exactly — no second
    GET, no second parse — even with fetch_full_text switched on.
    """
    subject = article(url="https://outlet.test/story/5")
    seen: list[httpx.Request] = []

    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            extracted=ExtractedMedia(article_text=LONG_PAGE),
            client=httpx.AsyncClient(transport=transport(
                {"/story/5": article_page(LONG_PAGE)}, seen
            )),
        )
    )

    assert stored is True
    assert subject.full_body_html
    assert seen == [], "the page was already fetched; it must not be fetched again"


def test_an_own_fetch_is_made_only_when_fetch_full_text_is_on():
    """
    The cost, stated plainly: one request per article for a licensed
    source whose page was not otherwise being fetched, and not one byte
    more than that.
    """
    subject = article(url="https://outlet.test/story/6")
    seen: list[httpx.Request] = []

    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            client=httpx.AsyncClient(transport=transport(
                {"/story/6": article_page(LONG_PAGE)}, seen
            )),
        )
    )

    assert stored is True
    assert subject.full_body_html
    assert [request.url.path for request in seen] == ["/story/6"]


# =============================================================
# Politeness
# =============================================================

def test_robots_txt_is_honoured_before_an_own_fetch():
    """
    The own-fetch path must ask robots.txt first, and a refusal must
    mean no request is sent at all — not a request that is sent and
    then apologised for afterwards.
    """
    subject = article(url="https://outlet.test/story/7")
    seen: list[httpx.Request] = []
    robots = FakeRobots(allowed=False)

    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            client=httpx.AsyncClient(transport=transport(
                {"/story/7": article_page(LONG_PAGE)}, seen
            )),
            robots=robots,
        )
    )

    assert stored is False
    assert subject.full_body_html is None
    assert robots.checked == ["https://outlet.test/story/7"]
    assert seen == []


def test_the_rate_limiter_is_waited_on_before_an_own_fetch():
    """
    An own-fetch is a request into a publisher, so it goes through the
    same per-host rate limiter every other request does. Skipping it is
    how a poll turns into a burst the outlet's edge will refuse.
    """
    subject = article(url="https://outlet.test/story/8")
    limiter = FakeRateLimiter()

    run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            client=httpx.AsyncClient(transport=transport(
                {"/story/8": article_page(LONG_PAGE)}
            )),
            robots=FakeRobots(allowed=True),
            rate_limiter=limiter,
        )
    )

    assert limiter.waits == 1


def test_a_robots_checker_that_raises_reads_as_not_allowed():
    """
    An unknown must read as "no". Failing open here would mean a broken
    robots fetch silently licenses a body fetch it should have refused.
    """
    class BrokenRobots:
        async def is_allowed(self, url, user_agent):
            raise httpx.ConnectError("robots.txt host is unreachable")

    subject = article(url="https://outlet.test/story/10")
    seen: list[httpx.Request] = []

    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            client=httpx.AsyncClient(transport=transport(
                {"/story/10": article_page(LONG_PAGE)}, seen
            )),
            robots=BrokenRobots(),
        )
    )

    assert stored is False
    assert subject.full_body_html is None
    assert seen == []


# =============================================================
# What actually gets stored
# =============================================================

def test_the_body_is_bounded():
    """
    One pathological page — a printer stylesheet scraped as prose, a
    comment thread — must not write a megabyte per article across 27
    outlets. The cap is the same constant the renderer applies, so what
    is stored is exactly what will be rendered.
    """
    subject = article()
    run(
        attach_full_body(
            subject,
            LICENSED,
            settings(),
            extracted=ExtractedMedia(article_text="word " * 40_000),
        )
    )

    assert len(subject.full_body_html or "") <= MAX_BODY_LENGTH


def test_a_stub_page_is_not_stored_as_a_body():
    """
    Below MIN_BODY_CHARS the recovered text is a nav menu, a cookie
    banner or a paywall stub, not an article. Storing it would replace
    a real excerpt with furniture. Falling back to the excerpt is
    strictly better.
    """
    assert body_text_from_extracted(ExtractedMedia(article_text="Read more")) is None
    assert body_text_from_extracted(ExtractedMedia(article_text="")) is None
    assert body_text_from_extracted(ExtractedMedia(article_text=None)) is None
    assert body_text_from_extracted(None) is None


def test_an_unreachable_page_stores_the_excerpt_not_nothing():
    """
    A body we could not fetch is not a licence to publish something
    else, and certainly not a reason to blank the article.
    """
    subject = article(url="https://outlet.test/story/11")

    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            client=httpx.AsyncClient(transport=transport({})),
        )
    )

    assert stored is False
    assert subject.full_body_html is None


def test_a_transport_failure_never_escapes():
    """
    Extraction is best-effort by definition. One article's body must
    never take down the rest of the feed.
    """
    class ExplodingClient:
        async def get(self, *args, **kwargs):
            raise httpx.ReadTimeout("publisher never answered")

        async def aclose(self):
            return None

    subject = article(url="https://outlet.test/story/12")

    stored = run(
        attach_full_body(
            subject,
            LICENSED,
            settings(enrich_body=True, fetch_full_text=True),
            client=ExplodingClient(),
        )
    )

    assert stored is False
    assert subject.full_body_html is None


def test_entities_are_decoded_and_tags_stripped():
    """
    Untrusted input is decoded exactly once and stored as text. `&amp;`
    must arrive at the reader as "&", and must not survive a second
    decode into "&".
    """
    text = to_plain_paragraphs(
        "<p>Tom &amp; Jerry said &quot;hello&quot; &amp;amp; left.</p>"
    )

    assert text == 'Tom & Jerry said "hello" &amp; left.'


def test_paragraph_breaks_survive_as_blank_lines():
    """
    Plain text can still carry structure, and _build_body() depends on
    it. Sanitising the whole document in one call collapses every run of
    whitespace to a single space, which welds an article into one
    run-on line — so the blocks are split first and rejoined after.
    """
    text = to_plain_paragraphs(page_text("First paragraph.", "Second paragraph."))

    assert text == "First paragraph.\n\nSecond paragraph."


# =============================================================
# The consumer contract
# =============================================================

def test_the_stored_body_renders_as_real_paragraphs_through_build_body():
    """
    End to end, producer to consumer: a plain-text body has to come out
    the other side of _build_body() as several paragraphs rather than
    one wall. This is what the article page's "read more" expands.

    _build_body() splits on block TAGS, which a markup-free body does
    not have — so a blank line has to be a boundary too. Without that,
    "paragraph breaks survive as plain text" was a claim the code did
    not honour.
    """
    class Article:
        full_body_html = page_text(
            "The first paragraph of the story, at some length.",
            "The second paragraph, carrying the detail that matters.",
            "The third paragraph, closing the piece out.",
        )

    body = _build_body(Article(), "The excerpt.")

    assert body.count("<p>") == 3, body
    assert "The second paragraph" in body


def test_build_body_still_rejects_a_markup_body_it_was_handed():
    """
    Defence in depth is not optional. _build_body() must escape and
    strip whatever it receives, because it runs after the producer and
    cannot re-check a licence it was handed.
    """
    class Article:
        full_body_html = (
            "<div><p>Legitimate first paragraph.</p>"
            "<script>alert(1)</script><img src=x onerror=alert(2)></div>"
        )

    body = _build_body(Article(), "The excerpt.")

    assert "<script" not in body
    assert "onerror" not in body
    assert "alert(" not in body
    assert "Legitimate first paragraph." in body


# =============================================================
# Length bound, at the source
# =============================================================

def test_a_body_longer_than_the_cap_is_truncated_not_dropped():
    """
    A 25k-character article is a long one, not a broken one. It must be
    stored truncated rather than refused, which is what a naive
    "too long, give up" would do to every feature story.
    """
    text = to_plain_paragraphs("word " * 30_000, max_length=1000)

    assert text is not None
    assert len(text) <= 1000


@pytest.mark.parametrize("value", [None, "", "   ", "<div></div>", "<p> </p>"])
def test_input_that_is_not_an_article_becomes_nothing(value):
    """
    The boundary cases, so a stub page cannot produce a body that reads
    as "read more" and then shows the reader nothing.
    """
    assert to_plain_paragraphs(value) is None
    assert body_text_from_extracted(ExtractedMedia(article_text=value)) is None
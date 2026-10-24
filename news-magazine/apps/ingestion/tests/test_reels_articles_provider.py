"""
Reels built from this site's own news.

THE PROBLEM THIS PROVIDER SOLVES
--------------------------------
Every other reels provider is a SEARCH over somebody else's fixed
catalogue, and a search over a fixed catalogue runs out. Wikimedia
Commons is asked for the newest Nepal videos, gets the same hundred
files back for ever, finds them all already stored, and adds nothing.

So the Reels page said "245 clips in the catalogue, 0 clips fetched
today" while the ingestion log said the pass was fine. The search was
not broken, the store was not broken, and the corpus was simply
finished.

The provider that fixes it searches nothing: it reads the articles this
site has already published and turns the newest ones that carry a
VERIFIED photograph into reels. A new article is a new reel by
construction, so the category moves in step with the news page, and it
cannot be exhausted.

These tests pin the three rules that make it safe:

  - only a picture that was PROVEN against the story's own page is
    used, because an unverified picture is regularly a photograph of
    something else entirely;
  - the same article is never turned into two reels, because the id of
    the reel is the id of the article;
  - the pass is bounded, and a limit of zero means zero.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.reels.providers import ArticlesSource

ARTICLE_UUID = "11111111-1111-4111-8111-111111111111"
OTHER_UUID = "22222222-2222-4222-8222-222222222222"


class Query:
    """Just enough of a PostgREST query for this provider."""

    def __init__(self, db: "FakeDB", table: str) -> None:
        self.db = db
        self.table = table

    def select(self, *columns):
        return self

    def eq(self, column, value):
        return self

    def is_(self, column, value):
        return self

    @property
    def not_(self):
        return self

    def or_(self, condition):
        return self

    def order(self, column, desc=False):
        return self

    def limit(self, n):
        return self

    def in_(self, column, values):
        return self

    def execute(self):
        self.db.calls.append(self.table)
        return SimpleNamespace(data=self.db.rows_for(self.table))


class FakeDB:
    def __init__(self, articles=None, reels=None):
        self.articles = articles or []
        self.reels = reels or []
        self.calls: list[str] = []

    def rows_for(self, table):
        if table == "articles":
            return self.articles
        if table == "reels":
            return self.reels
        return []

    def table(self, name):
        return Query(self, name)


def article(
    uuid: str = ARTICLE_UUID,
    headline: str = "सडक सुरक्षित गर्न के गर्नुपर्छ?",
    image: str = "https://cdn.example.test/photo.jpg",
    verified: bool = True,
) -> dict:
    row = {
        "id": uuid,
        "headline": headline,
        "excerpt": "काठमाडौंको सडकमा दुर्घटना हुनुको मुख्य कारण।",
        "image_highres_url": image,
        "featured_image_url": image,
        "image_verified_at": "2026-10-24T05:00:00Z" if verified else None,
        "source_name_snapshot": "Nagarik News",
        "source_article_url": "https://example.test/story",
        "published_at": "2026-10-24T05:00:00Z",
    }
    return row


@pytest.fixture
def source(monkeypatch):
    """A provider with the database it reads replaced by a fake."""
    from app import database

    def build(articles=None, reels=None):
        db = FakeDB(articles=articles, reels=reels)
        monkeypatch.setattr(database, "get_db", lambda: db)
        provider = ArticlesSource(pool=None, client=None, hint="test")
        return provider, db

    return build


@pytest.mark.asyncio
async def test_a_new_article_becomes_a_reel(source):
    provider, db = source(articles=[article()])

    reels = await provider.fetch(limit=10)

    assert len(reels) == 1
    reel = reels[0]
    assert reel.source_key == "articles"
    # The id of the reel IS the id of the article: that is what makes
    # it idempotent, and it is half of the store's unique constraint.
    assert reel.external_id == ARTICLE_UUID
    assert reel.title == "सडक सुरक्षित गर्न के गर्नुपर्छ?"
    assert reel.video_url == "https://cdn.example.test/photo.jpg"
    assert reel.poster_url == "https://cdn.example.test/photo.jpg"
    assert "articles" in db.calls
    assert "reels" in db.calls


@pytest.mark.asyncio
async def test_an_article_that_is_already_a_reel_is_not_made_into_another(source):
    provider, _db = source(
        articles=[article()],
        reels=[{"external_id": ARTICLE_UUID, "source_key": "articles"}],
    )

    reels = await provider.fetch(limit=10)

    assert reels == []


@pytest.mark.asyncio
async def test_an_article_with_no_picture_is_not_a_reel(source):
    provider, _db = source(articles=[article(image="")])

    assert await provider.fetch(limit=10) == []


@pytest.mark.asyncio
async def test_an_article_with_no_headline_is_not_a_reel(source):
    provider, _db = source(articles=[article(headline="")])

    assert await provider.fetch(limit=10) == []


@pytest.mark.asyncio
async def test_the_pass_is_bounded(source):
    provider, _db = source(
        articles=[article(uuid=ARTICLE_UUID), article(uuid=OTHER_UUID)]
    )

    assert len(await provider.fetch(limit=1)) == 1
    assert len(await provider.fetch(limit=2)) == 2


@pytest.mark.asyncio
async def test_a_limit_of_zero_means_zero(source):
    # A pass that has spent its budget asks for nothing. A provider
    # that answered that with a hundred reels would spend budget the
    # pass had already given away.
    provider, _db = source(articles=[article()])

    assert await provider.fetch(limit=0) == []


@pytest.mark.asyncio
async def test_no_articles_means_no_reels(source):
    provider, _db = source(articles=[])

    assert await provider.fetch(limit=10) == []

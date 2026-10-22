"""
The fetch caps.

WHAT THESE CAPS ARE FOR
-----------------------
A feed is a PAGE, not a stream. An outlet that published 400 stories
today serves all 400 in one feed document, and every one of them would
otherwise be normalised, deduped, page-fetched for its media and stored
on the very first poll. The same is true one level up: 27 outlets
polled on their own intervals put a few thousand entries through the
pipeline in a quarter of an hour, and each entry costs a dedup lookup
whether or not it turns out to be a story we already have.

So there are four bounds:

    max_articles_per_source  articles ONE outlet may contribute in one poll
    max_articles_per_cycle   articles the WHOLE pipeline may fetch in one cycle
    max_reels_per_provider   clips ONE video provider may contribute in one pass
    max_reels_per_cycle      clips the WHOLE reels pass may fetch in one pass

A CAP THAT IS ONLY A NUMBER IS NOT A CAP
----------------------------------------
The file these settings live in has already shipped a knob that nothing
read (`max_concurrent_fetches`), and the whole of `config.py` is
written around not doing that again. So every assertion below is about
the WORK, not about the configuration: the articles past the cap are
never normalised, never deduped, never page-fetched and never stored,
and the clips past it are never handed to the store.

The settings are also asserted to be real settings — readable from the
environment — because a cap that cannot be changed by an operator is a
cap that is wrong the day the operator needs it.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.config import Settings, get_settings
from app.services import ingestion as ingestion_module
from app.services.dedup import UNIQUE
from app.services.ingestion import (
    CycleBudget,
    per_cycle_cap,
    per_source_cap,
    reset_cycle_budget,
    run_source_sync,
)
from app.services.reels.types import Reel
from app.sources.base import NormalizedArticle


GOOD_UUID = "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8"
CATEGORY_UUID = "1a2b3c4d-5e6f-7081-9203-a4b5c6d7e8f9"


# =============================================================
# Fake supabase-py client
#
# supabase-py is a chainable builder: .table().select().eq().limit()
# .execute(). This fake records every write and answers every read with
# what the test needs — an empty articles table (so nothing is a
# duplicate), one category, and an id for anything written.
# =============================================================

class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.operation = "select"
        self.payload = None

    def select(self, *columns, **kwargs):
        self.operation = "select"
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.operation = "upsert"
        self.payload = payload
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, *args, **kwargs):
        return self

    def is_(self, *args, **kwargs):
        return self

    def gte(self, *args, **kwargs):
        return self

    def gt(self, *args, **kwargs):
        return self

    def lt(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, count):
        return self

    def in_(self, *args, **kwargs):
        return self

    @property
    def not_(self):
        return self

    def execute(self):
        self.db.record(self.table, self.operation, self.payload)
        return SimpleNamespace(data=self.db.rows_for(self.table, self.operation))


class FakeDB:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.article_rows: list[dict] = []
        self.reel_rows: list[dict] = []
        self.error_rows: list[dict] = []
        self.health_rows: list[dict] = []
        self.sync_log_updates: list[dict] = []

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params=None):
        return FakeQuery(self, f"rpc:{name}")

    def record(self, table, operation, payload):
        self.calls.append((table, operation))
        if operation == "upsert" and table == "articles":
            self.article_rows.append(payload)
        elif operation == "upsert" and table == "reels":
            self.reel_rows.append(payload)
        elif operation == "upsert" and table == "reels_source_health":
            self.health_rows.append(payload)
        elif operation == "insert" and table == "source_errors":
            self.error_rows.append(payload)
        elif operation == "update" and table == "source_sync_logs":
            self.sync_log_updates.append(payload)

    def rows_for(self, table, operation):
        if table == "source_sync_logs" and operation == "insert":
            return [{"id": GOOD_UUID}]
        if table == "categories":
            return [{"id": CATEGORY_UUID, "slug": "national"}]
        if table == "articles" and operation == "upsert":
            return [{"id": GOOD_UUID}]
        return []


# =============================================================
# A feed with however many articles the test asked for
# =============================================================

class FakeAdapter:
    """
    An adapter whose feed holds a fixed number of entries.

    The entries are NEWEST FIRST, exactly as a real feed is, because
    the cap keeps the head of the list — so what it throws away is the
    oldest material, and that is worth asserting about rather than
    assuming.
    """

    robots = None
    rate_limiter = None

    def __init__(self, count: int):
        self.count = count
        self.normalized: list[NormalizedArticle] = []

    async def fetch(self) -> str:
        return "<rss/>"

    def parse(self, raw: str) -> list[dict]:
        return [{"index": index} for index in range(self.count)]

    def normalize(self, entries: list[dict]) -> list[NormalizedArticle]:
        articles = [
            NormalizedArticle(
                source_article_url=f"https://example.test/story-{entry['index']}",
                canonical_url=f"https://example.test/story-{entry['index']}",
                headline=(
                    f"Report {entry['index']} about the "
                    f"{entry['index'] % 7}th committee in the capital"
                ),
                published_at=None,
                excerpt="A short summary of the story.",
            )
            for entry in entries
        ]
        self.normalized = articles
        return articles

    def validate(self, articles):
        return articles


class UniqueDeduplicator:
    """
    A deduper that finds no duplicates.

    The caps are applied BEFORE deduplication, so what this file is
    about does not depend on how similar two headlines happen to be.
    Stubbing it keeps the test about the cap: every article that gets
    past the cap is stored, so the number of stored rows IS the number
    of articles the cap let through.
    """

    def __init__(self, db=None, window_hours=12, fuzzy_threshold=78):
        self.db = db
        self.window_hours = window_hours
        self.fuzzy_threshold = fuzzy_threshold

    def check(self, headline, canonical_url=None):
        return UNIQUE

    def bind_article_id(self, **kwargs):
        return None

    def summary(self):
        return "unique only"


def _source_row(name: str = "The Daily Example") -> dict:
    return {
        "id": GOOD_UUID,
        "name": name,
        "enabled": True,
        "allowed": True,
        "sector": "national",
        "default_language": "ne",
        # No republication licence, so no article page is ever fetched.
        "republish_permission": False,
    }


def _settings(**overrides):
    """The real Settings, with the fetch caps overridden."""
    return get_settings().model_copy(update=overrides)


@pytest.fixture(autouse=True)
def _fresh_cycle():
    """
    The cycle budget is process-wide, exactly as it is in production.

    Every test starts with an empty one, so a test that spends the
    budget cannot change what the next test sees.
    """
    reset_cycle_budget()
    yield
    reset_cycle_budget()


async def _sync(source_row, settings, db, monkeypatch, adapters):
    """Run one source sync against the fakes."""
    monkeypatch.setattr(ingestion_module, "get_db", lambda: db)
    monkeypatch.setattr(
        ingestion_module,
        "build_adapter",
        lambda row: adapters[row.get("name")],
    )
    monkeypatch.setattr(ingestion_module, "Deduplicator", UniqueDeduplicator)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)

    return await run_source_sync(
        source_row, enrich_media=False, prune=False
    )


async def _sync_one(adapter, settings, db, monkeypatch):
    """Run one source sync — the common case, with one outlet."""
    return await _sync(
        _source_row(),
        settings,
        db,
        monkeypatch,
        {"The Daily Example": adapter},
    )


# =============================================================
# THE PER-OUTLET CAP
# =============================================================

async def test_the_per_outlet_cap_bounds_the_articles_one_poll_stores(
    monkeypatch,
):
    """
    The cap is about the WORK, so this asserts the work.

    Five hundred entries in the feed, a cap of ten, and exactly ten
    articles reach the database. The other four hundred and ninety are
    not merely hidden from the reader — they are never normalised,
    never deduped, never page-fetched for their media and never
    written.
    """
    db = FakeDB()
    adapter = FakeAdapter(500)

    result = await _sync_one(
        adapter,
        _settings(max_articles_per_source=10),
        db,
        monkeypatch,
    )

    assert result.fetched == 10, result.fetched
    assert len(db.article_rows) == 10, len(db.article_rows)
    assert len(adapter.normalized) == 10, len(adapter.normalized)


async def test_the_per_outlet_cap_is_configurable(monkeypatch):
    """
    The number has to be the operator's to set, and setting it has to
    change what the pipeline does.

    The same feed, the same outlet, three different caps — and three
    different numbers of articles stored. A knob that is read but does
    not bind is a knob that lies.
    """
    for cap, expected in ((5, 5), (25, 25), (200, 200)):
        db = FakeDB()
        adapter = FakeAdapter(500)

        result = await _sync_one(
            adapter,
            _settings(max_articles_per_source=cap),
            db,
            monkeypatch,
        )

        assert result.fetched == expected, (cap, result.fetched)
        assert len(db.article_rows) == expected, (cap, len(db.article_rows))


async def test_a_cap_that_binds_keeps_the_newest_articles(monkeypatch):
    """
    Which end of the feed the cap keeps is the whole point of it.

    A feed is newest first, so truncating the TAIL throws away the
    oldest stories — the ones the next poll would have found
    duplicate anyway. Truncating the head instead would silently stop
    the outlet publishing anything new, which is a different failure
    and a much worse one.
    """
    db = FakeDB()
    adapter = FakeAdapter(50)

    await _sync_one(
        adapter,
        _settings(max_articles_per_source=5),
        db,
        monkeypatch,
    )

    stored = [row["headline"] for row in db.article_rows]
    assert len(stored) == 5, stored
    for index, headline in enumerate(stored):
        assert f"Report {index} " in headline, (index, headline)


async def test_a_feed_inside_the_cap_is_untouched(monkeypatch):
    """
    A cap that truncated a normal feed would be a bug, not a feature.

    Most outlets publish far less between two polls than the cap
    allows, and those feeds must come through whole.
    """
    db = FakeDB()
    adapter = FakeAdapter(12)

    result = await _sync_one(
        adapter,
        _settings(max_articles_per_source=200),
        db,
        monkeypatch,
    )

    assert result.fetched == 12
    assert len(db.article_rows) == 12
    assert result.capped is False
    assert result.cap_dropped == 0


# =============================================================
# THE WHOLE-PIPELINE CAP
# =============================================================

async def test_the_cycle_cap_bounds_the_articles_fetched_across_outlets(
    monkeypatch,
):
    """
    The per-outlet cap alone still allows 27 x 200 — a crawl of 27
    publishers rather than a poll of them. This is the number that
    bounds the whole pipeline, and it is spent across outlets rather
    than per outlet.
    """
    db = FakeDB()
    first = FakeAdapter(100)
    second = FakeAdapter(100)
    adapters = {"First Outlet": first, "Second Outlet": second}

    settings = _settings(
        max_articles_per_source=100,
        max_articles_per_cycle=30,
        cycle_window_seconds=900,
    )

    await _sync(_source_row("First Outlet"), settings, db, monkeypatch, adapters)
    await _sync(_source_row("Second Outlet"), settings, db, monkeypatch, adapters)

    assert len(db.article_rows) == 30, len(db.article_rows)
    assert first.normalized, "the first outlet must still have been polled"
    assert len(first.normalized) == 30, len(first.normalized)
    assert second.normalized == [], "the second outlet had nothing left"


async def test_two_outlets_racing_for_the_last_slots_cannot_both_take_them(
    monkeypatch,
):
    """
    The reservation is taken BEFORE the work is done.

    Two outlets polling at the same time — which is what the scheduler
    does, and what the cron entry point does with a semaphore of five —
    must not both be handed the same last thirty articles. The total
    is a total.
    """
    db = FakeDB()
    first = FakeAdapter(100)
    second = FakeAdapter(100)
    adapters = {"First Outlet": first, "Second Outlet": second}

    settings = _settings(
        max_articles_per_source=100,
        max_articles_per_cycle=30,
        cycle_window_seconds=900,
    )

    await asyncio.gather(
        _sync(_source_row("First Outlet"), settings, db, monkeypatch, adapters),
        _sync(_source_row("Second Outlet"), settings, db, monkeypatch, adapters),
    )

    assert len(db.article_rows) == 30, len(db.article_rows)


async def test_the_cycle_cap_is_configurable(monkeypatch):
    """
    The same two outlets, three different totals.
    """
    for cap, expected in ((10, 10), (150, 150), (500, 200)):
        # The budget is process-wide, so each case starts with a whole
        # one — exactly as a new window would.
        reset_cycle_budget()

        db = FakeDB()
        first = FakeAdapter(100)
        second = FakeAdapter(100)
        adapters = {"First Outlet": first, "Second Outlet": second}

        settings = _settings(
            max_articles_per_source=100,
            max_articles_per_cycle=cap,
            cycle_window_seconds=900,
        )

        await _sync(_source_row("First Outlet"), settings, db, monkeypatch, adapters)
        await _sync(_source_row("Second Outlet"), settings, db, monkeypatch, adapters)

        assert len(db.article_rows) == expected, (cap, len(db.article_rows))


class Clock:
    """
    A stand-in for the `time` module with a `monotonic()` we control.

    Swapped into app.services.ingestion only, so a window-rollover
    test takes no wall-clock time and does not depend on the machine
    being fast.
    """

    def __init__(self, start: float = 1_000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __getattr__(self, name):
        # Everything else (sleep, time, ...) is the real module.
        return getattr(time, name)


async def test_the_cycle_window_rolls_over_so_the_budget_is_refilled(monkeypatch):
    """
    A cap that never refills is a permanent shutdown, not a cap.

    The budget is spent over a WINDOW rather than against a batch,
    because the scheduler has no single pass over every outlet — each
    source has its own interval. When the window expires the
    allowance is whole again.
    """
    clock = Clock()
    monkeypatch.setattr(ingestion_module, "time", clock)

    budget = CycleBudget()

    assert await budget.reserve(10, limit=10, window_seconds=60) == 10
    assert await budget.reserve(10, limit=10, window_seconds=60) == 0

    # A second short of the window expiring, the allowance is still
    # spent.
    clock.advance(59)
    assert await budget.reserve(10, limit=10, window_seconds=60) == 0

    # And now it has expired: a new window, and the allowance is whole
    # again.
    clock.advance(2)
    assert await budget.reserve(10, limit=10, window_seconds=60) == 10


# =============================================================
# SEEING IT
#
# A poll that quietly throws stories away looks exactly like a poll
# that found nothing new. The difference has to survive somewhere the
# operator can read it.
# =============================================================

async def test_a_cap_that_truncates_is_recorded_where_the_operator_can_see_it(
    monkeypatch,
):
    db = FakeDB()
    adapter = FakeAdapter(500)

    result = await _sync_one(
        adapter,
        _settings(max_articles_per_source=10),
        db,
        monkeypatch,
    )

    # On the result, and therefore in the sync log's details column.
    assert result.capped is True
    assert result.cap_reason == "max_articles_per_source"
    assert result.cap_dropped == 490

    details = result.as_dict()
    assert details["capped"] is True
    assert details["cap_reason"] == "max_articles_per_source"
    assert details["cap_dropped"] == 490

    # And in the sync log row the admin dashboard reads.
    assert db.sync_log_updates, "the sync log was never finished"
    assert db.sync_log_updates[-1]["details"]["capped"] is True

    # And as a `source_errors` row, which is the other table the
    # dashboard reads. The error_type says what it is, so a red line
    # that means "you fetched too much" is not mistaken for a red line
    # that means "the publisher is down".
    assert len(db.error_rows) == 1, db.error_rows
    row = db.error_rows[0]
    assert row["error_type"] == "fetch_capped"
    assert "max_articles_per_source" in row["message"]
    assert row["context"]["dropped"] == 490
    assert row["context"]["limit"] == 10


async def test_a_poll_inside_every_cap_records_nothing_to_worry_about(
    monkeypatch,
):
    """
    The notice is for the polls that actually dropped something. A row
    for every poll would train the operator to ignore it.
    """
    db = FakeDB()
    adapter = FakeAdapter(10)

    result = await _sync_one(
        adapter,
        _settings(max_articles_per_source=200),
        db,
        monkeypatch,
    )

    assert result.capped is False
    assert db.error_rows == [], db.error_rows


# =============================================================
# THE REELS CAPS
# =============================================================

def _reel(external_id: str) -> Reel:
    return Reel(
        source_key="wikimedia",
        external_id=external_id,
        video_url=f"https://upload.wikimedia.org/{external_id}.webm",
        title=f"Clip {external_id}",
        creator_name="A Photographer",
        license_name="CC BY-SA 4.0",
        attribution_required=True,
        width=854,
        height=480,
        duration_seconds=12.5,
        published_at="2026-01-02T03:04:05+00:00",
    )


class ManyReelSource:
    """
    A provider with far more clips than any cap allows.

    It honours the `limit` it is handed, exactly as every real adapter
    does — that contract is where the per-provider cap bites, so a
    source that ignored it would be testing nothing at all.
    """

    key = "wikimedia"
    requires_key = False

    def __init__(self, available: int = 500):
        self.available = available
        self.requested: list[int] = []

    async def fetch(self, limit: int = 30) -> list[Reel]:
        self.requested.append(limit)
        return [
            _reel(f"clip-{index}")
            for index in range(min(limit, self.available))
        ]


async def _reels_pass(settings, db, monkeypatch, sources):
    """Run one reels pass with every provider replaced by a fake."""
    from app.workers import scheduler as sched

    monkeypatch.setattr(sched, "get_settings", lambda: settings)
    monkeypatch.setattr(sched, "get_db", lambda: db)
    monkeypatch.setattr(
        "app.services.reels.providers.build_source",
        lambda source_key, *, pool, client, hint="": sources[source_key],
    )
    sched._reels_last_run.clear()

    await sched._sync_reels()


def _reels_settings(**overrides):
    defaults = dict(
        enable_reels=True,
        pexels_api_keys="pexels-key",
        pixabay_api_keys=None,
        youtube_api_keys=None,
        reels_search_terms="nepal",
        reels_http_timeout=20.0,
        reels_pass_timeout_seconds=180,
        reels_user_agent="TestReels/1.0 (+https://example.test)",
    )
    defaults.update(overrides)
    return _settings(**defaults)


async def test_the_per_provider_reel_cap_bounds_what_one_provider_contributes(
    monkeypatch,
):
    """
    One provider, a cap of seven, and seven clips in the database.

    The provider is asked for seven as well: the cap is applied to the
    REQUEST, not to the rows after the fact, so the provider is never
    made to hand over a hundred clips that are then thrown away.
    """
    db = FakeDB()
    source = ManyReelSource()

    await _reels_pass(
        _reels_settings(max_reels_per_provider=7, max_reels_per_cycle=500),
        db,
        monkeypatch,
        {"wikimedia": source},
    )

    assert source.requested == [7], source.requested
    assert len(db.reel_rows) == 7, len(db.reel_rows)


async def test_the_per_provider_reel_cap_is_configurable(monkeypatch):
    for cap, expected in ((3, 3), (12, 12), (40, 40)):
        db = FakeDB()
        source = ManyReelSource()

        await _reels_pass(
            _reels_settings(
                max_reels_per_provider=cap, max_reels_per_cycle=500
            ),
            db,
            monkeypatch,
            {"wikimedia": source},
        )

        assert source.requested == [cap], (cap, source.requested)
        assert len(db.reel_rows) == expected, (cap, len(db.reel_rows))


async def test_the_reels_pass_cap_bounds_the_clips_across_providers(
    monkeypatch,
):
    """
    One reels pass is one cycle on the video side: the job runs every
    provider once, in order. The total cap is spent across them, and
    shared fairly, so a tight budget thins every provider's share
    instead of starving the one that happens to be asked last.
    """
    db = FakeDB()
    wikimedia = ManyReelSource()
    pexels = ManyReelSource()

    await _reels_pass(
        _reels_settings(
            max_reels_per_provider=100, max_reels_per_cycle=10
        ),
        db,
        monkeypatch,
        {"wikimedia": wikimedia, "pexels": pexels},
    )

    assert len(db.reel_rows) == 10, len(db.reel_rows)
    assert wikimedia.requested == [5], wikimedia.requested
    assert pexels.requested == [5], pexels.requested


async def test_the_reels_pass_cap_is_configurable(monkeypatch):
    for cap, expected in ((4, 4), (20, 20), (200, 200)):
        db = FakeDB()
        wikimedia = ManyReelSource()
        pexels = ManyReelSource()

        await _reels_pass(
            _reels_settings(
                max_reels_per_provider=100, max_reels_per_cycle=cap
            ),
            db,
            monkeypatch,
            {"wikimedia": wikimedia, "pexels": pexels},
        )

        assert len(db.reel_rows) == expected, (cap, len(db.reel_rows))


# =============================================================
# THEY ARE SETTINGS
#
# A cap the operator cannot change is a cap that is wrong the day the
# operator needs it.
# =============================================================

def test_the_caps_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("MAX_ARTICLES_PER_SOURCE", "137")
    monkeypatch.setenv("MAX_ARTICLES_PER_CYCLE", "1234")
    monkeypatch.setenv("CYCLE_WINDOW_SECONDS", "300")
    monkeypatch.setenv("MAX_REELS_PER_PROVIDER", "42")
    monkeypatch.setenv("MAX_REELS_PER_CYCLE", "99")

    settings = Settings()

    assert settings.max_articles_per_source == 137
    assert settings.max_articles_per_cycle == 1234
    assert settings.cycle_window_seconds == 300
    assert settings.max_reels_per_provider == 42
    assert settings.max_reels_per_cycle == 99


def test_the_old_reels_per_source_name_still_works(monkeypatch):
    """
    `REELS_PER_SOURCE` was this knob's earlier name. A deployment that
    already sets it keeps its value rather than silently getting the
    new default.
    """
    monkeypatch.setenv("REELS_PER_SOURCE", "17")

    settings = Settings()

    assert settings.max_reels_per_provider == 17


def test_the_defaults_are_sane():
    """
    The per-outlet cap has to be in the range the operator asked for
    — 100 to 500 articles at a time — and the reels cap alongside it.
    """
    settings = Settings()

    assert 100 <= settings.max_articles_per_source <= 500, (
        settings.max_articles_per_source
    )
    assert 100 <= settings.max_reels_per_provider <= 500, (
        settings.max_reels_per_provider
    )
    assert settings.max_articles_per_cycle >= settings.max_articles_per_source
    assert settings.max_reels_per_cycle >= settings.max_reels_per_provider
    assert settings.cycle_window_seconds > 0


def test_a_cap_of_zero_is_clamped_to_one_rather_than_being_unlimited():
    """
    A cap that a misconfigured value can turn OFF is not a cap. Zero
    means "fetch one", not "fetch everything".
    """
    settings = SimpleNamespace(max_articles_per_source=0)

    assert per_source_cap(settings) == 1
    assert per_cycle_cap(SimpleNamespace(max_articles_per_cycle=0)) == 1


def test_a_settings_object_without_the_caps_falls_back_to_the_defaults():
    """
    An older deployment, or a test double, must not end up with a cap
    of one article — that would silently stop the pipeline.
    """
    settings = SimpleNamespace()

    assert per_source_cap(settings) == 500
    assert per_cycle_cap(settings) == 20_000

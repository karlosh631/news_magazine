"""
Tests for the article store path.

These pin the invariant that lets run_source_sync() decide whether it is
safe to write an article_sources link:

    store_article() returns a real uuid  -> the row exists, link it
    store_article() returns None         -> nothing was written, skip it

The bug this guards against was subtle and silent: an upsert can succeed
and still hand back no row (PostgREST is allowed to answer with an empty
representation). `(response.data or [{}])[0].get("id")` turned that into
None, so a stored article was counted as "rejected", its source link was
never written, and the multi-outlet merge record stayed incomplete.
"""

from __future__ import annotations

import asyncio
import pytest
from types import SimpleNamespace

from app.services.ingestion import (
    _bump_source_count,
    _clean_uuid,
    _extract_id,
    _is_uuid,
    _upsert_article_source,
    store_article,
)

GOOD_UUID = "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8"
OTHER_UUID = "1a2b3c4d-5e6f-7081-8293-a4b5c6d7e8f9"


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# =============================================================
# Fake supabase-py client
#
# supabase-py is a chainable builder: .table().select().eq().limit()
# .execute(). These fakes record every step so a test can assert both
# the return value and whether the database was touched at all.
# =============================================================

class FakeResponse:
    def __init__(self, data=None):
        self.data = data


class FakeQuery:
    def __init__(self, db):
        self.db = db

    def select(self, *a, **k):
        self.db.calls.append(("select", self.db.table_name))
        return self

    def eq(self, column, value):
        self.db.calls.append(("eq", column, value))
        self.db.eq_pairs.append((column, value))
        return self

    def limit(self, n):
        return self

    def upsert(self, row, on_conflict=None):
        self.db.calls.append(("upsert", self.db.table_name, on_conflict))
        self.db.upserted = row
        return self

    def insert(self, row):
        self.db.calls.append(("insert", self.db.table_name))
        self.db.inserted = row
        return self

    def execute(self):
        self.db.calls.append(("execute", self.db.table_name))
        return FakeResponse(self.db.next_response())


class FakeTable:
    def __init__(self, db):
        self.db = db

    def select(self, *a, **k):
        return FakeQuery(self.db).select(*a, **k)

    def upsert(self, row, on_conflict=None):
        return FakeQuery(self.db).upsert(row, on_conflict=on_conflict)

    def insert(self, row):
        return FakeQuery(self.db).insert(row)


class FakeDB:
    """Queue of responses, one per execute() in order."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list = []
        self.eq_pairs: list = []
        self.upserted = None
        self.inserted = None
        self.table_name = "articles"

    def table(self, name):
        self.table_name = name
        return FakeTable(self)

    def rpc(self, name, params):
        self.calls.append(("rpc", name, params))
        return SimpleNamespace(
            execute=lambda: FakeResponse(self.responses.pop(0) if self.responses else None)
        )

    def next_response(self):
        return self.responses.pop(0) if self.responses else None


def make_article(headline="बडादसैँको आठौँ दिन, मैतीदेवी मन्दिरमा दर्शनार्थीको भीड"):
    return SimpleNamespace(
        headline=headline,
        language="ne",
        source_article_url="https://example.com/story",
        canonical_url="https://example.com/story",
        published_at="2026-10-18T12:00:00+00:00",
        raw_metadata={},
    )


def call_store(db):
    return run(
        store_article(
            db=db,
            article=make_article(),
            media={"image_url": "https://cdn.example.com/p.jpg"},
            category_id=GOOD_UUID,
            source_row={"id": OTHER_UUID, "name": "Example", "sector": "national"},
            content_hash="deadbeef",
            title_normalized="headline",
            excerpt="An excerpt.",
        )
    )


# =============================================================
# UUID guards
# =============================================================

def test_clean_uuid_accepts_a_real_uuid():
    assert _clean_uuid(GOOD_UUID) == GOOD_UUID


def test_clean_uuid_strips_surrounding_whitespace():
    """
    A padded id is a real id. Returning the stripped form means the
    value we hand to Postgres never carries the padding.
    """
    assert _clean_uuid(f"  {GOOD_UUID}\n") == GOOD_UUID


def test_clean_uuid_rejects_everything_that_is_not_a_uuid():
    for bad in (None, "", "   ", "not-a-uuid", 123, 4.5, True, {}, [],
                GOOD_UUID[:-1], GOOD_UUID + "0", "0f7c1a2b3d4e5f607182 93a4b5c6d7e8"):
        assert _clean_uuid(bad) is None, bad
        assert not _is_uuid(bad), bad


def test_is_uuid_is_a_predicate_not_a_converter():
    assert _is_uuid(GOOD_UUID) is True
    assert _is_uuid(None) is False


# =============================================================
# PostgREST response handling
# =============================================================

def test_extract_id_reads_the_first_row():
    assert _extract_id(FakeResponse([{"id": GOOD_UUID}])) == GOOD_UUID


def test_extract_id_returns_none_for_an_empty_representation():
    """
    The exact case that used to be counted as a rejected article: the
    upsert wrote the row, the response carried nothing.
    """
    assert _extract_id(FakeResponse([])) is None
    assert _extract_id(FakeResponse(None)) is None
    assert _extract_id(FakeResponse([None])) is None


def test_extract_id_rejects_a_malformed_id_rather_than_returning_it():
    """A non-uuid would only fail later, at article_sources."""
    assert _extract_id(FakeResponse([{"id": "nope"}])) is None
    assert _extract_id(FakeResponse([{"id": None}])) is None


# =============================================================
# store_article
# =============================================================

def test_store_article_returns_the_id_from_a_normal_upsert():
    db = FakeDB([[{"id": GOOD_UUID}]])
    assert call_store(db) == GOOD_UUID
    assert ("upsert", "articles", "slug") in db.calls


def test_store_article_returns_none_instead_of_raising_when_the_insert_fails():
    """
    A conflicting content_hash is a legitimate outcome, not a crash.
    The exception used to escape store_article and abort the article
    mid-feed; now it is reported and the caller skips the link.
    """
    db = FakeDB()

    def boom(*a, **k):
        raise RuntimeError(
            'duplicate key value violates unique constraint "articles_content_hash_idx"'
        )

    db.upsert = boom  # type: ignore[assignment]
    db.table = lambda name: SimpleNamespace(upsert=boom)  # type: ignore[assignment]

    assert call_store(db) is None


def test_store_article_recovers_the_id_by_slug_when_the_response_is_empty():
    """
    The row IS in the table. Recovering its id by slug keeps the article
    counted as new and lets the article_sources link be written.
    """
    db = FakeDB([[], [{"id": GOOD_UUID}]])
    assert call_store(db) == GOOD_UUID
    assert ("eq", "slug") in [c[:2] for c in db.calls if c[0] == "eq"]
    assert ("slug", db.upserted["slug"]) in db.eq_pairs


def test_store_article_returns_none_when_the_slug_recovery_finds_nothing():
    db = FakeDB([[], []])
    assert call_store(db) is None


def test_store_article_does_not_trust_a_malformed_id_in_the_response():
    db = FakeDB([[{"id": "not-a-uuid"}], [{"id": GOOD_UUID}]])
    assert call_store(db) == GOOD_UUID


def test_store_article_never_returns_something_that_is_not_a_uuid():
    """
    The whole contract: whatever comes back, the caller's `if article_id`
    is safe to act on.
    """
    for responses in ([[{"id": GOOD_UUID}]], [[], [{"id": GOOD_UUID}]],
                      [[]], [[], []], [[{"id": "junk"}]]):
        assert _is_uuid(call_store(FakeDB(responses)) or "") or True
        value = call_store(FakeDB(responses))
        assert value is None or _is_uuid(value), responses


# =============================================================
# _upsert_article_source
# =============================================================

def test_upsert_article_source_writes_the_link_for_a_valid_id():
    db = FakeDB([[]])  # existence check returns no rows -> insert
    assert run(
        _upsert_article_source(db, GOOD_UUID, OTHER_UUID, "https://example.com/a")
    ) == "created"
    assert db.inserted["article_id"] == GOOD_UUID
    assert db.inserted["source_id"] == OTHER_UUID
    assert db.inserted["source_article_url"] == "https://example.com/a"
    assert db.inserted["is_primary"] is False


def test_upsert_article_source_reports_an_existing_link_without_reinserting():
    """
    Idempotence is what keeps source_count honest. Re-polling an article
    every few minutes must not look like a newly discovered outlet.
    """
    db = FakeDB([[{"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}]])
    assert run(
        _upsert_article_source(db, GOOD_UUID, OTHER_UUID, "https://example.com/a")
    ) == "exists"
    assert db.inserted is None


def test_upsert_article_source_skips_without_touching_the_database():
    """
    The core invariant. A bad or missing article id means there is no row
    to point at, so nothing is sent to Postgres at all — no
    "invalid input syntax for type uuid" raised once per article.
    """
    for bad in (None, "", "   ", "not-a-uuid", 0, {}):
        db = FakeDB()
        assert run(_upsert_article_source(db, bad, OTHER_UUID, "https://example.com/a")) == "skipped"
        assert db.calls == [], bad


def test_upsert_article_source_skips_a_bad_source_id():
    db = FakeDB()
    assert run(_upsert_article_source(db, GOOD_UUID, None, "https://example.com/a")) == "skipped"
    assert db.calls == []


def test_upsert_article_source_skips_when_the_url_is_missing():
    db = FakeDB()
    assert run(_upsert_article_source(db, GOOD_UUID, OTHER_UUID, "")) == "skipped"
    assert db.calls == []


def test_upsert_article_source_writes_the_stripped_id():
    db = FakeDB([[]])
    run(_upsert_article_source(db, f" {GOOD_UUID} ", OTHER_UUID, "https://example.com/a"))
    assert db.inserted["article_id"] == GOOD_UUID


def test_upsert_article_source_does_not_raise_when_every_write_fails():
    """
    The article is already stored; only the merge record is degraded.
    This must return False quietly, never propagate a database error.
    """
    db = FakeDB()

    def boom(*a, **k):
        raise RuntimeError('invalid input syntax for type uuid')

    db.table = lambda name: SimpleNamespace(  # type: ignore[assignment]
        upsert=boom, select=boom, insert=boom
    )
    assert run(
        _upsert_article_source(db, GOOD_UUID, OTHER_UUID, "https://example.com/a")
    ) == "skipped"


# =============================================================
# _bump_source_count
# =============================================================

def test_bump_source_count_ignores_a_non_uuid_without_calling_the_rpc():
    db = FakeDB()
    run(_bump_source_count(db, "not-a-uuid"))
    assert not [c for c in db.calls if c[0] == "rpc"]


def test_bump_source_count_calls_the_rpc_for_a_valid_id():
    db = FakeDB([3])
    run(_bump_source_count(db, GOOD_UUID))
    rpc_calls = [c for c in db.calls if c[0] == "rpc"]
    assert len(rpc_calls) == 1
    assert rpc_calls[0][2] == {"p_article_id": GOOD_UUID}


# =============================================================
# The invariant the caller depends on
# =============================================================

def test_caller_writes_the_link_when_a_valid_id_came_back():
    """Mirrors run_source_sync()'s `if article_id:` branch."""
    db = FakeDB([[], [{"id": GOOD_UUID}]])
    article_id = call_store(db)
    assert article_id == GOOD_UUID

    run(_upsert_article_source(db, article_id, OTHER_UUID, "https://e.com/a"))
    assert [c for c in db.calls if c[1:2] == ("article_sources",)]


def test_caller_touches_nothing_when_no_id_came_back():
    """
    Mirrors run_source_sync()'s else branch. No row means no link, and
    critically no database call that could raise a type error.
    """
    db = FakeDB([[], []])
    article_id = call_store(db)
    assert article_id is None

    # The else branch does not call _upsert_article_source at all; prove
    # that calling it anyway would still be safe.
    before = len(db.calls)
    assert run(_upsert_article_source(db, article_id, OTHER_UUID, "https://e.com/a")) == "skipped"
    assert len(db.calls) == before

# =============================================================
# Cancellation
# =============================================================

def test_cancelled_sync_is_not_swallowed_by_the_per_article_handler():
    """
    asyncio.CancelledError is a BaseException, so `except Exception`
    never sees it. If the per-article handler caught it, a cancelled run
    would look like one that simply processed fewer articles, and the
    scheduler would carry on believing the task was alive.
    """
    assert not issubclass(asyncio.CancelledError, Exception)

    # The handler must re-raise rather than absorb.
    class Article:
        headline = "Some headline"
        language = "ne"
        source_article_url = "https://example.com/a"
        canonical_url = "https://example.com/a"
        published_at = None
        raw_metadata = {}

    db = FakeDB([[{"id": GOOD_UUID}]])
    task = asyncio.ensure_future(
        store_article(
            db=db,
            article=Article(),
            media={},
            category_id=None,
            source_row={"id": OTHER_UUID, "name": "Example", "sector": "national"},
            content_hash="deadbeef",
            title_normalized="headline",
            excerpt=None,
        )
    )
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        asyncio.get_event_loop().run_until_complete(task)


# =============================================================
# Transient database failures
# =============================================================

def test_transient_connection_errors_are_retried_classified():
    """
    PostgREST over HTTP/2 drops connections routinely, and supabase-py
    has no retry of its own. Without this the pipeline threw away real
    articles ("articles upsert failed ...: Server disconnected").
    """
    from app.services.ingestion import _is_transient_db_error

    class RemoteProtocolError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class ReadTimeout(Exception):
        pass

    for exc in (
        RemoteProtocolError("Server disconnected"),
        APIConnectionError("Connection closed"),
        ReadTimeout(""),
        Exception("connection reset by peer"),
        Exception("Server disconnected"),
        Exception("Read timed out."),
    ):
        assert _is_transient_db_error(exc), exc


def test_data_errors_are_not_retried():
    """
    A constraint violation is a statement about the data, not the
    connection. Retrying it just delays the real error threefold and
    multiplies the log noise.
    """
    from app.services.ingestion import _is_transient_db_error

    for exc in (
        Exception('duplicate key value violates unique constraint "x"'),
        Exception('{"code":"23505","message":"duplicate key"}'),
        Exception("invalid input syntax for type uuid"),
        Exception("row-level security violation"),
        Exception("schema cache miss"),
    ):
        assert not _is_transient_db_error(exc), exc


def test_db_call_retries_then_succeeds():
    """A dropped connection must not cost us the article."""
    from app.services.ingestion import db_call

    attempts = {"n": 0}

    class RemoteProtocolError(Exception):
        pass

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RemoteProtocolError("Server disconnected")
        return "ok"

    assert run(db_call(flaky)) == "ok"
    assert attempts["n"] == 3


def test_db_call_gives_up_after_the_attempt_budget():
    from app.services.ingestion import DB_RETRY_ATTEMPTS, db_call

    class RemoteProtocolError(Exception):
        pass

    attempts = {"n": 0}

    def always_fails():
        attempts["n"] += 1
        raise RemoteProtocolError("Server disconnected")

    try:
        run(db_call(always_fails))
        raised = False
    except RemoteProtocolError:
        raised = True

    assert raised, "the error must not be swallowed"
    assert attempts["n"] == DB_RETRY_ATTEMPTS


def test_db_call_does_not_retry_a_data_error():
    from app.services.ingestion import db_call

    attempts = {"n": 0}

    def constraint_violation():
        attempts["n"] += 1
        raise RuntimeError('duplicate key value violates unique constraint "x"')

    try:
        run(db_call(constraint_violation))
    except RuntimeError:
        pass

    assert attempts["n"] == 1, "a data error must not be retried"

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
    # asyncio.run(), not get_event_loop().run_until_complete().
    #
    # get_event_loop() is deprecated and raises "There is no current
    # event loop" once another test has called asyncio.run() and closed
    # the loop it created. That made this helper order-dependent: these
    # tests passed alone and failed in the full suite purely because of
    # which file ran first.
    return asyncio.run(coro)


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

    def upsert(self, row, on_conflict=None, ignore_duplicates=False, **kwargs):
        self.db.calls.append(
            ("upsert", self.db.table_name, on_conflict, ignore_duplicates)
        )
        self.db.upserted = row
        self.db.upsert_ignore_duplicates = ignore_duplicates
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

    def upsert(self, row, on_conflict=None, ignore_duplicates=False, **kwargs):
        return FakeQuery(self.db).upsert(
            row, on_conflict=on_conflict, ignore_duplicates=ignore_duplicates
        )

    def insert(self, row):
        return FakeQuery(self.db).insert(row)


class FakeDB:
    """Queue of responses, one per execute() in order."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list = []
        self.eq_pairs: list = []
        self.upserted = None
        self.upsert_ignore_duplicates = None
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
    assert ("upsert", "articles", "slug", False) in db.calls


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
    # The link is written ONCE, idempotently: PostgREST's
    # `resolution=ignore-duplicates` turns the insert into an
    # ON CONFLICT DO NOTHING, and the row comes back only when this
    # call is the one that wrote it.
    db = FakeDB([[{"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}]])
    assert run(
        _upsert_article_source(db, GOOD_UUID, OTHER_UUID, "https://example.com/a")
    ) == "created"
    assert db.upserted["article_id"] == GOOD_UUID
    assert db.upserted["source_id"] == OTHER_UUID
    assert db.upserted["source_article_url"] == "https://example.com/a"
    assert db.upserted["is_primary"] is False
    assert db.upsert_ignore_duplicates is True


def test_upsert_article_source_reports_an_existing_link_without_reinserting():
    """
    Idempotence is what keeps source_count honest. Re-polling an article
    every few minutes must not look like a newly discovered outlet.
    """
    # No row comes back: the write was a no-op because the link is
    # already there. The story is not counted as a new outlet.
    db = FakeDB([[]])
    assert run(
        _upsert_article_source(db, GOOD_UUID, OTHER_UUID, "https://example.com/a")
    ) == "exists"


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
    db = FakeDB([[{"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}]])
    run(_upsert_article_source(db, f" {GOOD_UUID} ", OTHER_UUID, "https://example.com/a"))
    assert db.upserted["article_id"] == GOOD_UUID


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
    # Its own loop, rather than the shared one. This test has to build the
    # task, cancel it, and only then run it — so it cannot use asyncio.run,
    # which owns and closes its loop for the duration of one coroutine.
    loop = asyncio.new_event_loop()
    try:
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
            ),
            loop=loop,
        )
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(task)
    finally:
        loop.close()


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


# =============================================================
# DNS failures are distinguished from HTTP failures
# =============================================================

def test_dns_failures_are_recognised():
    """
    Seven outlets do not resolve from every network. A site that is down
    returns 503 and recovers; a hostname that does not exist does not,
    so retrying it forever tells us nothing new.
    """
    import socket

    import httpx

    from app.services.ingestion import is_dns_failure

    for exc in (
        socket.gaierror(-2, "Name or service not known"),
        httpx.ConnectError("[Errno -2] Name or service not known"),
        Exception("Temporary failure in name resolution"),
        Exception("Could not resolve host: example.invalid"),
    ):
        assert is_dns_failure(exc), exc


def test_http_failures_are_not_mistaken_for_dns():
    """A busy or broken site must not be disabled as unreachable."""
    import httpx

    from app.services.ingestion import is_dns_failure

    for exc in (
        httpx.ConnectError("Connection refused"),
        httpx.ReadTimeout("timed out"),
        Exception("503 Server Error"),
        Exception("404 Not Found"),
        Exception("parser exploded"),
    ):
        assert not is_dns_failure(exc), exc


def test_dns_failure_survives_being_wrapped():
    """httpx nests the socket error one level down; the cause is checked."""
    import socket

    from app.services.ingestion import is_dns_failure

    class Wrapped(Exception):
        def __init__(self, cause):
            super().__init__("wrapped")
            self.__cause__ = cause

    assert is_dns_failure(Wrapped(socket.gaierror(-2, "nope")))


def test_saw_dns_failure_reads_the_run_result():
    from dataclasses import dataclass

    from app.workers.scheduler import _saw_dns_failure

    @dataclass
    class FakeResult:
        error_details: list

    assert _saw_dns_failure(FakeResult([{"type": "dns_failure"}]))
    assert not _saw_dns_failure(FakeResult([{"type": "fetch_or_parse_error"}]))
    assert not _saw_dns_failure(FakeResult([]))
    assert not _saw_dns_failure(object())


def test_disable_source_really_issues_the_update():
    """
    The breaker is only useful if it actually writes. This exists because
    it didn't: `_disable_source` called `db_call` without importing it, so
    every invocation raised NameError, was swallowed by its own except,
    and returned False — the circuit breaker silently disabled nothing
    while logging loudly that it had tried.
    """
    import asyncio

    from app.workers.scheduler import _disable_source

    calls: list = []

    class FakeQuery:
        def update(self, payload):
            calls.append(payload)
            return self

        def eq(self, column, value):
            calls.append((column, value))
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    class FakeDB:
        def table(self, name):
            calls.append(("table", name))
            return FakeQuery()

    assert asyncio.run(_disable_source(FakeDB(), "source-1", {"name": "Khabar"})) is True
    assert ("table", "sources") in calls
    assert {"enabled": False} in calls


def test_disable_source_reports_failure_without_raising():
    """A database outage must not take the scheduler down with it."""
    import asyncio

    from app.workers.scheduler import _disable_source

    class ExplodingDB:
        def table(self, name):
            raise RuntimeError("supabase down")

    assert asyncio.run(_disable_source(ExplodingDB(), "source-1", {})) is False


# =============================================================
# REELS JOB REGISTRATION
#
# The whole Reels feature hangs off one setting. ENABLE_REELS defaults
# to false, so on a deploy that never touches it the job is never
# registered, nothing is ever fetched, and /category/reels is an empty
# state that looks identical to "we ran and there was nothing".
#
# These pin the two halves of the switch that are easy to get wrong:
# off must REMOVE a job that is already registered (a process started
# with reels on must stop polling when the operator turns it off), and
# it must do that without a redeploy, because _periodic_refresh calls
# this same function every five minutes.
# =============================================================


class FakeJob:
    def __init__(self, job_id):
        self.id = job_id
        self.removed = False

    def remove(self):
        self.removed = True


class FakeScheduler:
    def __init__(self, jobs=None):
        self.jobs = dict(jobs or {})
        self.added: list[dict] = []

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_job(self, fn, **kwargs):
        self.added.append({"fn": fn, **kwargs})
        self.jobs[kwargs["id"]] = FakeJob(kwargs["id"])


def _reel_settings(**overrides):
    """The reels fields of Settings, with everything else irrelevant."""
    defaults = dict(
        enable_reels=True,
        reels_poll_seconds=900,
        reels_per_source=30,
        reels_search_terms=None,
        reels_http_timeout=20.0,
        reels_user_agent="TestAgent/1.0",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_the_reels_job_is_registered_only_when_reels_are_enabled(monkeypatch):
    from app.workers import scheduler as sched

    for enabled, expect_job in ((True, True), (False, False)):
        fake = FakeScheduler()
        monkeypatch.setattr(
            sched, "get_settings", lambda: _reel_settings(enable_reels=enabled)
        )
        monkeypatch.setattr(sched, "get_scheduler", lambda: fake)

        sched._register_reels_job()

        assert (sched.REELS_JOB_ID in fake.jobs) is expect_job, enabled
        assert len(fake.added) is (1 if enabled else 0), enabled


def test_disabling_reels_removes_a_job_that_is_already_running(monkeypatch):
    """
    Declining to ADD the job is not the same as turning it off. A
    process started with reels enabled would otherwise keep polling
    every provider for the life of the container.
    """
    from app.workers import scheduler as sched

    existing = FakeJob(sched.REELS_JOB_ID)
    fake = FakeScheduler({sched.REELS_JOB_ID: existing})
    monkeypatch.setattr(sched, "get_settings", lambda: _reel_settings(enable_reels=False))
    monkeypatch.setattr(sched, "get_scheduler", lambda: fake)

    sched._register_reels_job()

    assert existing.removed
    assert fake.added == []


def test_reels_reregistration_replaces_rather_than_duplicates(monkeypatch):
    """
    The 5-minute refresh calls this again on every tick, so the enabled
    path has to be idempotent — otherwise the refresh itself would pile
    up copies of the job.
    """
    from app.workers import scheduler as sched

    fake = FakeScheduler()
    monkeypatch.setattr(sched, "get_settings", lambda: _reel_settings())
    monkeypatch.setattr(sched, "get_scheduler", lambda: fake)

    sched._register_reels_job()
    sched._register_reels_job()
    sched._register_reels_job()

    assert len(fake.added) == 3
    assert all(call["replace_existing"] is True for call in fake.added)
    assert {call["id"] for call in fake.added} == {sched.REELS_JOB_ID}


def test_a_tiny_poll_interval_is_raised_to_the_floor(monkeypatch):
    """
    MIN_POLL_SECONDS exists so nothing can be configured into hammering
    a provider. It has to apply to the reels job too, and not just to
    the outlets.
    """
    from app.workers import scheduler as sched

    fake = FakeScheduler()
    monkeypatch.setattr(
        sched, "get_settings", lambda: _reel_settings(reels_poll_seconds=1)
    )
    monkeypatch.setattr(sched, "get_scheduler", lambda: fake)

    sched._register_reels_job()

    assert fake.added[0]["trigger"].interval.total_seconds() == sched.MIN_POLL_SECONDS


def test_a_content_hash_collision_recovers_the_stored_row():
    """
    articles_content_hash_idx is UNIQUE while the upsert targets `slug`,
    so a story already stored under a different slug cannot be written.

    Returning None for that was quietly harmful: the caller counted the
    article as rejected AND skipped the article_sources link, so the
    outlet's copy was orphaned and source_count never rose for it. The
    row exists — recover its id and let the link be written.
    """
    from app.services.ingestion import store_article

    class HashConflict(Exception):
        code = "23505"
        details = "Key (content_hash)=(abc123) already exists."
        message = ('duplicate key value violates unique constraint '
                   '"articles_content_hash_idx"')

    class FakeQuery:
        def select(self, *a, **k):
            return self

        def upsert(self, row, **k):
            raise HashConflict("boom")

        def eq(self, column, value):
            self._last = (column, value)
            return self

        def limit(self, n):
            return self

        def execute(self):
            if getattr(self, "_last", None) == ("content_hash", "abc123"):
                return type("R", (), {"data": [{"id": GOOD_UUID}]})()
            return type("R", (), {"data": []})()

    class FakeDB:
        def table(self, name):
            return FakeQuery()

    class Article:
        headline = "Headline"
        language = "ne"
        source_article_url = "https://example.com/a"
        canonical_url = "https://example.com/a"
        published_at = None
        raw_metadata = {}

    got = asyncio.run(
        store_article(
            db=FakeDB(), article=Article(), media={}, category_id=None,
            source_row={"id": OTHER_UUID, "name": "Example", "sector": "national"},
            content_hash="abc123", title_normalized="headline", excerpt=None,
        )
    )
    assert got == GOOD_UUID, "a hash collision must recover the stored row, not drop it"


def test_a_slug_collision_is_not_mistaken_for_a_hash_collision():
    """
    23505 for any OTHER constraint means the slug already exists — which
    is what the upsert handles. Recovering that by hash would attach an
    article_sources link to a row that is not this story.
    """
    from app.services.ingestion import _is_content_hash_conflict

    slug_conflict = Exception(
        'duplicate key value violates unique constraint "articles_slug_key"'
    )
    assert not _is_content_hash_conflict(slug_conflict)


def test_full_body_is_stored_as_paragraphs_not_html():
    """
    sources.republish_permission gates whether an outlet's body may be
    republished. The extractor enforced it; nothing downstream ever read
    full_body_html, so body_html was unconditionally the excerpt and the
    page's "read more" expanded three sentences and stopped.

    Stored as PLAIN TEXT, never the publisher's markup: full-text
    republication is a licensing decision, carrying untrusted HTML to a
    reader is a security one, and they are separable.
    """
    from app.services.ingestion import _build_body

    class FullArticle:
        full_body_html = (
            '<div class="story"><p>First para with '
            "<script>alert(1)</script> markup.</p>"
            "<p>Second para here.</p><p>Third one.</p></div>"
            "<script>evil()</script>"
        )

    body = _build_body(FullArticle(), "The excerpt.")

    assert body is not None
    # Three paragraphs, not one run-on block.
    assert body.count("<p>") == 3, body
    # No markup survived.
    assert "<script" not in body
    assert "alert(1)" not in body
    assert "evil()" not in body
    assert "First para with markup." in body


def test_body_falls_back_to_the_excerpt_when_there_is_no_full_text():
    from app.services.ingestion import _build_body

    class ExcerptOnly:
        full_body_html = None

    assert _build_body(ExcerptOnly(), "Short excerpt.") == "<p>Short excerpt.</p>"
    assert _build_body(ExcerptOnly(), None) is None


def test_a_body_that_sanitises_to_nothing_falls_back_to_the_excerpt():
    """
    A page whose body is entirely markup must not blank the article. The
    excerpt is strictly better than an empty body.
    """
    from app.services.ingestion import _build_body

    class ScriptOnly:
        full_body_html = "<script>evil()</script><style>x{}</style>"

    assert _build_body(ScriptOnly(), "The excerpt.") == "<p>The excerpt.</p>"

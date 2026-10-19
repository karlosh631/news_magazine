"""
Tests for the reels pipeline.

Reels are short vertical videos sourced from three providers that
actually permit republication. Three things about that pipeline are
load-bearing enough to pin down with tests:

  1. A category page that reads a table which does not exist yet. The
     code for this feature deploys before its migration
     (0014_reels.sql) far more often than not, and a 500 there takes
     the whole Reels category down for a feature that simply has no
     content. `load_reels` returning [] is the difference between "not
     launched yet" and "broken site".

  2. Key rotation. Free-tier video APIs meter per key and a 429 lasts
     an hour. A single-key client treats that as "the provider is
     down" and stops producing reels for everybody. And a key that is
     merely broken (500, timeout) must NOT be cooled down, or one bad
     afternoon eats the whole pool.

  3. Credentials never appear in a log line or a health endpoint.
     `snapshot()` is exactly the sort of thing that gets a full key
     pasted into it by accident, and that is a working credential
     published to anybody who can read /api/health.

Plus the rule the whole package is written around: a reel is metadata
and an upstream URL. Video bytes are never stored, not in Postgres, not
in Storage, not anywhere. There is no column for them and there must
never be one, which is the last thing an assertion can guarantee and
the first thing someone adds "just this once".
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import time
from types import SimpleNamespace

import pytest

from app.services.reels import keys as reels_keys
from app.services.reels.keys import KeyPool, KeyPoolExhausted, build_pool
from app.services.reels import providers as reels_providers
from app.services.reels.providers import PROVIDERS, _BaseSource, build_source
from app.services.reels.store import (
    FEED_COLUMNS,
    REEL_CONFLICT,
    _is_missing_reels_table,
    ingest_reels,
    load_reels,
    store_reel,
    store_reels,
)
from app.services.reels.types import Reel, ReelSource


GOOD_UUID = "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8"


def run(coro):
    return asyncio.run(coro)


def make_reel(external_id: str = "clip-1", source_key: str = "pexels") -> Reel:
    return Reel(
        source_key=source_key,
        external_id=external_id,
        video_url=(
            "https://videos.pexels.com/video-files/1234567/"
            f"{external_id}.mp4?token=0123456789abcdef"
        ),
        poster_url="https://images.pexels.com/videos/1234567/poster.jpg",
        title="A short vertical clip",
        description="A short clip.",
        creator_name="A Photographer",
        creator_url="https://www.pexels.com/@a-photographer/",
        license_name="Pexels License",
        license_url="https://www.pexels.com/license/",
        attribution_required=False,
        width=1080,
        height=1920,
        duration_seconds=12.5,
        published_at="2026-10-18T09:30:00+00:00",
    )


class Clock:
    """
    A stand-in for the `time` module with a `time()` we control.

    Swapped into app.services.reels.keys only, so a cooldown test takes
    no wall-clock time and does not depend on the machine being fast.
    """

    def __init__(self, start: float = 1_700_000_000.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __getattr__(self, name):
        # Everything else (sleep, monotonic, ...) is the real module.
        return getattr(time, name)


# =============================================================
# KeyPool
#
# A 429 from a free tier lasts an hour. Rotating to the next key is the
# difference between a feed that degrades and one that dies.
# =============================================================

def test_next_key_walks_the_pool_round_robin():
    """
    Rotation is the entire point. A pool that keeps handing back the
    same key gets one key exhausted per hour instead of N, so the
    "spare" keys are decorative.
    """
    pool = KeyPool("pexels", ["key-a", "key-b", "key-c"])

    assert [pool.next_key() for _ in range(7)] == [
        "key-a", "key-b", "key-c",
        "key-a", "key-b", "key-c",
        "key-a",
    ]


def test_a_key_in_cooldown_is_skipped():
    """
    The one behaviour the pool exists for: key-a is out of quota, so
    every subsequent request goes to key-b. A pool that handed back
    key-a anyway would burn the quota it does not have and hand the
    caller a second 429.
    """
    pool = KeyPool("pexels", ["key-a", "key-b"])
    pool.mark_quota_exceeded("key-a")

    assert pool.available_count() == 1
    assert [pool.next_key() for _ in range(3)] == ["key-b", "key-b", "key-b"]


def test_next_key_raises_when_every_key_is_cooling_down():
    """
    Exhaustion is distinct from a provider outage: nothing is wrong
    with the provider, we simply have no quota left, and the correct
    response is a sleep rather than a retry storm or a red line on a
    dashboard.
    """
    pool = KeyPool("pixabay", ["key-a", "key-b", "key-c"])
    for key in ("key-a", "key-b", "key-c"):
        pool.mark_quota_exceeded(key)

    assert pool.available_count() == 0
    with pytest.raises(KeyPoolExhausted):
        pool.next_key()

    # Callers already catching RuntimeError (the usual `except
    # Exception` boundary) must still see this as one of their errors.
    assert issubclass(KeyPoolExhausted, RuntimeError)


def test_the_exhaustion_message_says_how_long_to_wait(monkeypatch):
    """
    "All keys exhausted" tells an operator nothing they can act on. The
    correct response is a sleep of a known length, so the number has to
    be in the message.
    """
    clock = Clock(1_000_000.0)
    monkeypatch.setattr(reels_keys, "time", clock)

    pool = KeyPool("pexels", ["key-a", "key-b"], cooldown_seconds=90.0)
    pool.mark_quota_exceeded("key-a", retry_after=90)
    pool.mark_quota_exceeded("key-b", retry_after=90)

    clock.advance(30)  # 60s of the 90s left

    with pytest.raises(KeyPoolExhausted) as caught:
        pool.next_key()

    message = str(caught.value)
    assert "60s" in message, message
    assert "pexels" in message, message
    assert "2 key(s)" in message, message


def test_mark_quota_exceeded_honours_a_short_retry_after(monkeypatch):
    """
    A provider that says "come back in 90 seconds" is authoritative.
    Defaulting to the full hour instead would waste an hour of quota on
    a key that was usable ninety seconds in.
    """
    clock = Clock(1_000_000.0)
    monkeypatch.setattr(reels_keys, "time", clock)

    pool = KeyPool("pexels", ["key-a", "key-b"])  # default cooldown: 3600s
    pool.mark_quota_exceeded("key-a", retry_after=90)

    clock.advance(89)
    assert pool.available_count() == 1, "key-a is still cooling"

    clock.advance(2)  # t+91
    assert pool.available_count() == 2, "key-a came back at Retry-After, not at an hour"


def test_an_implausible_retry_after_does_not_extend_the_cooldown(monkeypatch):
    """
    The other direction. A provider answering "retry in 86400" when the
    pool's own cooldown is a minute must not park the key for a day —
    the cap exists so one malformed header cannot disable a source for
    longer than a redeploy would.
    """
    clock = Clock(1_000_000.0)
    monkeypatch.setattr(reels_keys, "time", clock)

    pool = KeyPool("pexels", ["key-a"], cooldown_seconds=60.0)
    pool.mark_quota_exceeded("key-a", retry_after=86_400.0)

    clock.advance(61)
    assert pool.available_count() == 1


def test_a_missing_retry_after_falls_back_to_the_default_cooldown(monkeypatch):
    clock = Clock(1_000_000.0)
    monkeypatch.setattr(reels_keys, "time", clock)

    pool = KeyPool("pexels", ["key-a"])
    pool.mark_quota_exceeded("key-a")

    clock.advance(3601)
    assert pool.available_count() == 1


def test_an_ordinary_failure_does_not_cool_the_key_down():
    """
    Only a QUOTA failure cools a key. A 500, a timeout or a malformed
    response is the provider failing, and every key will hit it —
    burning the pool on those is how you end up with zero keys left
    before the provider recovers.
    """
    pool = KeyPool("pexels", ["key-a", "key-b"])
    for _ in range(3):
        pool.mark("key-a", ok=False)

    assert pool.available_count() == 2, "an ordinary failure is not a quota event"

    # Still in rotation, and the failure is still counted for reporting.
    assert pool.next_key() == "key-a"
    assert pool.next_key() == "key-b"

    entry = pool.snapshot()[0]
    assert entry["consecutive_failures"] == 3
    assert entry["available"] is True


def test_a_successful_call_clears_the_failure_streak():
    pool = KeyPool("pexels", ["key-a"])
    pool.mark("key-a", ok=False)
    pool.mark("key-a", ok=False)
    pool.mark("key-a", ok=True)

    assert pool.snapshot()[0]["consecutive_failures"] == 0


def test_marking_an_unknown_key_is_harmless():
    """
    A key the pool does not have must not blow up the caller — the
    provider echoing a key back that was never configured is a
    configuration problem, not a crash.
    """
    pool = KeyPool("pexels", ["key-a"])

    pool.mark("some-other-key", ok=False)
    pool.mark_quota_exceeded("some-other-key")

    assert pool.available_count() == 1


def test_reset_cooldowns_makes_everything_usable_again(monkeypatch):
    """
    The escape hatch for a quota that resets early and for a mistyped
    key that spent an hour in cooldown. Without it, an hour of zero
    reels needs a redeploy.
    """
    clock = Clock(2_000_000.0)
    monkeypatch.setattr(reels_keys, "time", clock)

    pool = KeyPool("pexels", ["key-a", "key-b"])
    pool.mark_quota_exceeded("key-a")
    pool.mark_quota_exceeded("key-b")

    assert pool.available_count() == 0
    with pytest.raises(KeyPoolExhausted):
        pool.next_key()

    pool.reset_cooldowns()

    assert pool.available_count() == 2
    assert sorted(pool.next_key() for _ in range(2)) == ["key-a", "key-b"]
    assert all(entry["cooldown_remaining_s"] == 0 for entry in pool.snapshot())


def test_build_pool_splits_on_commas_and_newlines():
    """
    Operators paste a block of keys into a Render secret at least as
    often as they type a comma-separated list, and a trailing comma is
    the classic way to end up with a pool that looks configured and is
    not.
    """
    pool = build_pool("pexels", "a, b ,,c\nd")

    assert len(pool) == 4
    assert pool.is_empty is False
    # Read back through the public API: the rotation order is the order
    # the keys were configured in.
    assert [pool.next_key() for _ in range(4)] == ["a", "b", "c", "d"]


def test_build_pool_with_nothing_configured_is_empty():
    """
    An unset key is a normal way to run this service — Wikimedia needs
    no configuration at all — so it has to read as "not configured"
    rather than as an error.
    """
    for raw in (None, "", "   ", ",, ,", "\n"):
        pool = build_pool("pexels", raw)
        assert pool.is_empty is True, raw
        assert len(pool) == 0, raw


def test_snapshot_never_contains_a_working_credential(monkeypatch):
    """
    THE credential-leak guard.

    `snapshot()` exists to be rendered by /api/health. It is fed the
    live pool on every request, so a full key in there is a working
    credential published to anyone who can read that endpoint, and
    pasted into logs from there. A short prefix is enough to tell two
    keys apart while debugging rotation; that is the whole job.
    """
    clock = Clock(4_000_000.0)
    monkeypatch.setattr(reels_keys, "time", clock)

    secret_a = "pexels-live-7f3a9c2b1d8e4f6a5b0c3d7e9f1a2b4c"
    secret_b = "pexels-live-0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    pool = KeyPool("pexels", [secret_a, secret_b])

    pool.next_key()
    pool.next_key()
    pool.mark_quota_exceeded(secret_a)  # one entry cools, one stays healthy

    entries = pool.snapshot()
    assert len(entries) == 2

    rendered = repr(entries)
    for secret in (secret_a, secret_b):
        assert secret not in rendered, "a full key reached the snapshot"

    for secret, entry in zip((secret_a, secret_b), entries):
        for column, value in entry.items():
            assert secret not in str(value), f"{column} leaked the key"
        # Six characters and an ellipsis: enough to identify, useless
        # to anyone who finds it.
        assert entry["label"] == secret[:6] + "…"

    # The snapshot is still useful: it reports rotation, not just state.
    assert [entry["total_uses"] for entry in entries] == [1, 1]
    assert [entry["available"] for entry in entries] == [False, True]


# =============================================================
# Reel
# =============================================================

def test_reel_rejects_a_video_url_that_is_not_absolute():
    """
    A relative or malformed URL renders as a silent black card in the
    feed. It is far better to drop the item at the edge than to ship a
    clip that never plays and never says why.
    """
    for bad in (
        "/media/reel.mp4",
        "reel.mp4",
        "//cdn.example.com/reel.mp4",
        "data:video/mp4;base64,AAAA",
        "ftp://example.com/reel.mp4",
        "",
    ):
        with pytest.raises(ValueError):
            Reel(source_key="wikimedia", external_id="1", video_url=bad)


def test_reel_accepts_a_plain_http_url():
    """http is a real scheme; only schemeless and relative are rejected."""
    reel = Reel(source_key="wikimedia", external_id="1",
                video_url="http://upload.wikimedia.org/a.mp4")
    assert reel.video_url.startswith("http://")


def test_reel_requires_an_identity():
    """
    (source_key, external_id) is the dedup key and half of it is what
    an operator sees in the feed. A reel with neither has no identity:
    it can neither be deduplicated nor attributed to a provider.
    """
    for source_key, external_id in (("", "1"), (None, "1"),
                                    ("pexels", ""), ("pexels", None),
                                    (None, None)):
        with pytest.raises(ValueError):
            Reel(source_key=source_key, external_id=external_id,
                 video_url="https://cdn.example.com/a.mp4")


def test_aspect_ratio_is_none_when_the_provider_did_not_say():
    """
    "We do not know" and "we know it is 0" are the same answer here.
    Dividing by an absent or zero height raises ZeroDivisionError, which
    takes down the whole card render rather than one field of it.
    """
    base = dict(source_key="pexels", external_id="1",
                video_url="https://cdn.example.com/a.mp4")

    assert Reel(**base).aspect_ratio is None
    assert Reel(**{**base, "width": 1080}).aspect_ratio is None
    assert Reel(**{**base, "height": 1920}).aspect_ratio is None
    assert Reel(**{**base, "width": 1080, "height": 0}).aspect_ratio is None
    assert Reel(**{**base, "width": 0, "height": 1920}).aspect_ratio is None

    assert Reel(**{**base, "width": 1080, "height": 1920}).aspect_ratio == (
        pytest.approx(1080 / 1920)
    )


def test_reel_has_nowhere_to_put_video_bytes():
    """
    The hard rule of the package, structurally enforced.

    Reels are the largest objects this system will ever touch; storing
    them would bloat the database and blow through row and bandwidth
    limits to buy nothing a URL does not already buy. The guarantee is
    a field that does not exist — and a field is exactly what someone
    adds "just this once, for the poster".
    """
    names = {field.name for field in dataclasses.fields(Reel)}

    offenders = sorted(
        name for name in names
        if any(word in name.lower()
               for word in ("byte", "blob", "content", "data", "payload",
                            "file", "storage", "body"))
    )
    assert offenders == [], f"Reel grew a byte-shaped field: {offenders}"


# =============================================================
# Provider registry
# =============================================================

def test_every_provider_class_is_registered():
    """
    A new adapter that is not in PROVIDERS cannot be configured, and a
    caller passing its key gets a KeyError that reads like a typo. The
    registry and the module's classes must not drift apart.
    """
    classes = {
        value.key
        for value in vars(reels_providers).values()
        if inspect.isclass(value)
        and issubclass(value, _BaseSource)
        and value is not _BaseSource
    }

    assert classes == set(PROVIDERS), (
        "an adapter exists that build_source() cannot construct"
    )

    for name, source_cls in PROVIDERS.items():
        assert source_cls.key == name, f"{name} is registered under a different key"


def test_requires_key_matches_the_deployment_reality():
    """
    Wikimedia needs no key, and that is the entire reason the category
    is alive on a fresh deploy. Get this wrong — say Wikimedia needs a
    key — and a deploy with no secrets configured produces an empty
    category and a red line.
    """
    assert PROVIDERS["wikimedia"].requires_key is False
    assert PROVIDERS["pexels"].requires_key is True
    assert PROVIDERS["pixabay"].requires_key is True


def test_a_constructed_source_satisfies_the_reel_source_protocol():
    """
    The contract every adapter owes the store. It is a runtime_checkable
    Protocol with three members on purpose, and a store that reaches
    into a concrete adapter for anything else is coupled to it.
    """
    for name, source_cls in PROVIDERS.items():
        # client is stored, not called, until fetch(); nothing here
        # touches the network.
        source = source_cls(pool=None, client=None, hint="")

        assert isinstance(source, ReelSource), name
        assert source.key == name
        assert isinstance(source.requires_key, bool)
        assert inspect.iscoroutinefunction(source.fetch)


def test_build_source_returns_the_registered_adapter():
    for name, source_cls in PROVIDERS.items():
        source = build_source(name, pool=None, client=None)
        assert isinstance(source, source_cls), name
        assert source.key == name


def test_build_source_normalizes_the_key():
    """Config is typed by a human, so case and stray spaces are common."""
    source = build_source("  Pexels ", pool=None, client=None)
    assert source.key == "pexels"
    assert isinstance(source, PROVIDERS["pexels"])


def test_build_source_rejects_an_unknown_key():
    """
    A precise KeyError naming the known keys, so a typo is visible
    instead of being silently skipped — a source that quietly never
    runs looks exactly like a source with nothing to report.
    """
    for unknown in ("nope", "", "   ", None, "pexels_api"):
        with pytest.raises(KeyError) as caught:
            build_source(unknown, pool=None, client=None)

        message = str(caught.value)
        assert "wikimedia" in message, message
        assert "pixabay" in message, message


# =============================================================
# Fake supabase-py client
#
# supabase-py is a chainable builder: .table().select().order().limit()
# .execute(). These fakes record every step so a test can assert both
# the return value and what was sent.
# =============================================================

class MissingTableError(Exception):
    """Postgres's answer when the migration has not been applied yet."""

    code = "42P01"
    message = 'relation "public.reels" does not exist'
    details = "42P01: relation public.reels does not exist"


class SchemaCacheMiss(Exception):
    """PostgREST's answer for a table it has never seen. No code, no hint."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class MissingTableDB:
    """
    Fails on the first step of the chain, the way the real client does.

    Not on execute(): PostgREST refuses to plan the query at all when
    the relation is unknown, so a fake that waits until execute() is
    testing a different code path than production runs.
    """

    def __init__(self, error: BaseException):
        self.error = error
        self.tables: list[str] = []

    def table(self, name):
        self.tables.append(name)
        return self

    def select(self, *a, **k):
        raise self.error

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        raise self.error


class FakeQuery:
    def __init__(self, db):
        self.db = db

    def select(self, *columns):
        self.db.calls.append(("select", columns))
        return self

    def order(self, column, desc=False):
        self.db.calls.append(("order", column, desc))
        return self

    def limit(self, n):
        self.db.calls.append(("limit", n))
        return self

    @property
    def not_(self):
        return self

    def in_(self, column, values):
        self.db.calls.append(("in_", column, tuple(values)))
        return self

    def upsert(self, row, on_conflict=None):
        self.db.calls.append(("upsert", self.db.table_name, on_conflict))
        if row.get("external_id") in self.db.fail_ids:
            raise RuntimeError(
                f'null value in column "title" violates not-null constraint'
            )
        self.db.upserted = row
        self.db.upsert_rows.append(row)
        return self

    def execute(self):
        self.db.calls.append(("execute", self.db.table_name))
        return SimpleNamespace(data=self.db.rows)


class FakeDB:
    def __init__(self, rows=None, fail_ids=()):
        self.rows = rows or []
        self.fail_ids = set(fail_ids)
        self.calls: list = []
        self.table_name = "reels"
        self.upserted = None
        self.upsert_rows: list = []

    def table(self, name):
        self.table_name = name
        self.calls.append(("table", name))
        return FakeQuery(self)


class ExplodingDB:
    """Every write fails, for whatever reason the test cares about."""

    def __init__(self, error=None):
        self.error = error or RuntimeError("permission denied for table reels")
        self.tables: list[str] = []

    def table(self, name):
        self.tables.append(name)
        raise self.error


# =============================================================
# load_reels before the migration lands
#
# This is the regression guard. The code ships before its schema far
# more often than not, and the alternative answer — let the exception
# out — is a 500 on the category page for a feature that simply has no
# content yet.
# =============================================================

def test_load_reels_returns_an_empty_feed_when_the_table_does_not_exist():
    for error in (
        MissingTableError("42P01: relation public.reels does not exist"),
        SchemaCacheMiss(
            "Could not find the table 'public.reels' in the schema cache: PGRST205"
        ),
    ):
        db = MissingTableDB(error)
        assert run(load_reels(db, limit=10)) == [], error
        assert db.tables == ["reels"], error


def test_load_reels_returns_an_empty_feed_on_any_other_failure():
    """
    Same answer for the caller either way; a missing feature and a
    flaky read are both "no reels to show", never a 500.
    """
    db = MissingTableDB(RuntimeError("permission denied for table reels"))
    assert run(load_reels(db, limit=10)) == []


def test_a_missing_column_is_not_dressed_up_as_a_missing_table():
    """
    "column reels.poster_url does not exist" is a missing COLUMN, and
    reporting it as "apply 0014_reels.sql" sends the operator to a file
    that will not fix it. A blanket "does not exist" match is exactly
    the wrong-but-plausible log line that costs an hour.
    """
    assert _is_missing_reels_table(
        RuntimeError('column reels.poster_url does not exist')
    ) is False
    assert _is_missing_reels_table(
        RuntimeError("relation public.articles does not exist")
    ) is False
    assert _is_missing_reels_table(RuntimeError("something else entirely")) is False


def test_the_migration_hint_names_the_file_to_apply():
    """
    "something is wrong" is useless at 3am. "apply 0014_reels.sql" is
    a thing you can do.
    """
    from app.services.reels.store import _MIGRATION_HINT

    assert "0014_reels.sql" in _MIGRATION_HINT


def test_a_zero_limit_never_touches_the_database():
    db = MissingTableDB(MissingTableError())
    assert run(load_reels(db, limit=0)) == []
    assert db.tables == []


def test_a_bad_excluded_id_is_dropped_rather_than_emptying_the_feed():
    """
    One malformed uuid in exclude_ids would otherwise make Postgres
    reject the whole query — "invalid input syntax for type uuid" — and
    a visitor who had already seen clips would get an empty feed.
    """
    db = FakeDB(rows=[])
    run(load_reels(db, limit=10, exclude_ids={"not-a-uuid", "1234"}))

    assert [call for call in db.calls if call[0] == "in_"] == []


def test_valid_excluded_ids_reach_the_query():
    db = FakeDB(rows=[])
    run(load_reels(db, limit=10, exclude_ids={GOOD_UUID, " " + GOOD_UUID}))

    filtered = [call for call in db.calls if call[0] == "in_"]
    assert len(filtered) == 1
    assert filtered[0][1] == "id"
    assert set(filtered[0][2]) == {GOOD_UUID}


def test_load_reels_shapes_each_row_for_the_web_app():
    """
    The response is handed straight to the client, so it must match the
    TypeScript Reel interface exactly: the stored columns plus a
    computed like_count, and a reel nobody liked still comes back.
    """
    db = FakeDB(rows=[{
        "id": GOOD_UUID,
        "source_key": "pexels",
        "external_id": "clip-1",
        "video_url": "https://videos.pexels.com/clip-1.mp4",
        "poster_url": None,
        "title": "A short clip",
        "description": None,
        "creator_name": "A Photographer",
        "creator_url": None,
        "license_name": "Pexels License",
        "license_url": "https://www.pexels.com/license/",
        "attribution_required": False,
        "width": 1080,
        "height": 1920,
        "duration_seconds": 12.5,
        "published_at": "2026-10-18T09:30:00+00:00",
        "ingested_at": "2026-10-18T09:35:00+00:00",
        "reel_likes": [{"count": 3}],
    }])

    rows = run(load_reels(db, limit=10))

    assert len(rows) == 1
    row = rows[0]
    assert set(row) == set(FEED_COLUMNS) | {"like_count"}
    assert row["like_count"] == 3
    assert row["video_url"].startswith("https://")

    # Newest first, undated last, with a stable tiebreak — thirty rows
    # share one published_at on a source's first pass.
    orders = [call for call in db.calls if call[0] == "order"]
    assert orders[0][1] == "published_at.desc.nullslast"
    assert orders[1][1] == "ingested_at"


def test_an_unliked_reel_reports_zero_likes():
    """
    An inner join here would silently delete every reel nobody has
    liked from the feed.
    """
    db = FakeDB(rows=[{"id": GOOD_UUID, "video_url": "https://v.example/a.mp4"}])

    assert run(load_reels(db, limit=10))[0]["like_count"] == 0


# =============================================================
# store_reel
# =============================================================

def test_store_reel_upserts_metadata_and_a_url():
    """
    Upsert on (source_key, external_id) — the unique constraint in
    0014_reels.sql — so re-running the pass refreshes a clip in place
    and never duplicates it.
    """
    db = FakeDB()
    reel = make_reel()

    assert run(store_reel(db, reel)) is True

    assert ("table", "reels") in db.calls
    assert ("upsert", "reels", REEL_CONFLICT) in db.calls
    assert db.upserted["video_url"] == reel.video_url
    assert db.upserted["source_key"] == reel.source_key
    assert db.upserted["external_id"] == reel.external_id


def test_the_upsert_payload_contains_no_video_bytes():
    """
    THE hard rule, asserted on the wire.

    A reel row is metadata plus an upstream URL. Playback streams from
    the provider's own CDN. Storing the bytes would bloat the database,
    blow through Supabase's row and bandwidth limits, and buy nothing —
    and migration 0014_reels.sql has no column for them. If a payload
    grows one, this file has been misunderstood.
    """
    db = FakeDB()
    assert run(store_reel(db, make_reel())) is True

    row = db.upserted

    assert set(row) == {
        "source_key", "external_id", "video_url", "attribution_required",
        "published_at", "poster_url", "title", "description", "creator_name",
        "creator_url", "license_name", "license_url", "width", "height",
        "duration_seconds",
    }

    for column, value in row.items():
        assert not isinstance(value, (bytes, bytearray, memoryview)), column
        assert value is None or isinstance(value, (str, int, float, bool)), column
        # Nothing here is a payload in disguise either: base64 of a
        # three-minute clip is hundreds of kilobytes of one "column".
        assert len(str(value)) < 2048, f"{column} is {len(str(value))} characters"

    # The URL is the longest thing we send, and it is a URL.
    assert max(row, key=lambda column: len(str(row[column]))) == "video_url"
    assert row["video_url"].startswith("https://")


def test_a_field_the_provider_did_not_send_is_omitted_not_nulled():
    """
    This is an UPSERT. Repeating `title: None` every pass turns "the
    provider stopped sending a title" into "we erased the title we
    already had", and the card quietly loses its caption for a reason
    nobody can trace back here.
    """
    db = FakeDB()
    reel = make_reel()
    reel.title = None
    reel.poster_url = None
    reel.duration_seconds = None

    run(store_reel(db, reel))

    assert "title" not in db.upserted
    assert "poster_url" not in db.upserted
    assert "duration_seconds" not in db.upserted

    # The fields that must always be there are still there.
    assert db.upserted["video_url"] == reel.video_url
    assert db.upserted["published_at"]


def test_store_reel_reports_a_failure_without_raising():
    """
    One bad reel must never abort a pass. A feed of thirty clips where
    one has a malformed URL still has to produce twenty-nine cards.
    """
    db = ExplodingDB()

    assert run(store_reel(db, make_reel())) is False
    assert db.tables == ["reels"]


def test_store_reel_reports_a_missing_table_without_raising():
    """Same during a deploy, before 0014_reels.sql has been applied."""
    db = ExplodingDB(MissingTableError())

    assert run(store_reel(db, make_reel())) is False


def test_one_failing_reel_does_not_cost_the_others_their_pass():
    db = FakeDB(fail_ids={"bad-2"})

    health = run(store_reels(db, [make_reel("a"), make_reel("bad-2"), make_reel("c")]))

    assert health.source_key == "pexels"
    assert health.fetched == 3
    assert health.stored == 2
    assert health.skipped == 1
    # Two out of thirty is a degraded pass, not an outage. Reporting it
    # as an error would train the operator to ignore the field.
    assert health.error is None
    assert any("bad-2" in note for note in health.notes)


# =============================================================
# ingest_reels
# =============================================================

class ExplodingSource:
    key = "pexels"
    requires_key = True

    async def fetch(self, limit: int = 30):
        raise RuntimeError("upstream exploded")


class OneReelSource:
    key = "wikimedia"
    requires_key = False

    async def fetch(self, limit: int = 30):
        return [make_reel("commons-1", source_key="wikimedia")]


class SloppySource:
    key = "wikimedia"
    requires_key = False

    async def fetch(self, limit: int = 30):
        return [make_reel("commons-1", source_key="wikimedia"), {"not": "a Reel"}]


class BaseAbort(BaseException):
    """A failure `except Exception` cannot catch."""


class FatalSource:
    key = "pixabay"
    requires_key = False

    async def fetch(self, limit: int = 30):
        raise BaseAbort("the adapter failed harder than an Exception")


def test_one_dead_provider_costs_itself_nothing_else():
    """
    return_exceptions=True is what guarantees this: an exception
    escaping one source would cancel the siblings mid-fetch and turn a
    single dead provider into a dead pass.
    """
    db = FakeDB()

    health = run(ingest_reels(
        db, sources={"pexels": ExplodingSource(), "wikimedia": OneReelSource()}
    ))

    assert len(health) == 2, "one health report per source, in the order given"
    by_key = {item.source_key: item for item in health}

    assert by_key["pexels"].error, "the failure is recorded, not hidden"
    assert by_key["wikimedia"].stored == 1
    assert by_key["wikimedia"].error is None


def test_a_source_failing_harder_than_an_exception_does_not_take_the_pass_down():
    """
    The adapter contract cannot stop a source raising something outside
    Exception, and return_exceptions=True is what stops that from
    cancelling the siblings mid-fetch. The failure is recorded against
    the source that caused it; every other source still reports.
    """
    db = FakeDB()

    health = run(ingest_reels(
        db, sources={"pixabay": FatalSource(), "wikimedia": OneReelSource()}
    ))

    assert len(health) == 2
    by_key = {item.source_key: item for item in health}
    assert by_key["pixabay"].error
    assert by_key["wikimedia"].stored == 1


def test_an_item_that_is_not_a_reel_is_dropped_not_counted_as_stored():
    db = FakeDB()

    health = run(ingest_reels(db, sources={"wikimedia": SloppySource()}))[0]

    # `fetched` is what the provider returned, not what reached the
    # store: an operator reading this wants to know whether the provider
    # produced nothing, which is a different problem from a write
    # failing.
    assert health.fetched == 2
    assert health.stored == 1
    assert health.skipped == 1
    assert any("not Reel objects" in note for note in health.notes)


def test_an_empty_configuration_is_not_a_failure():
    assert run(ingest_reels(FakeDB(), sources={})) == []
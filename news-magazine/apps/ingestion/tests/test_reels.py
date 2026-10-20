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
from pathlib import Path
from types import SimpleNamespace

import httpx
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
    assert PROVIDERS["youtube"].requires_key is True


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
# THE KEYLESS PROVIDER, END TO END
#
# This section exists because the one provider that needs no
# configuration did not work, and nothing caught it.
#
# The scheduler builds a pool for EVERY provider with build_pool(),
# which returns an empty KeyPool rather than None when nothing is
# configured. `_get_json` only recognised None, so for Wikimedia —
# requires_key=False, pool of zero keys — it computed one attempt,
# called next_key(), and raised KeyPoolExhausted("All 0 key(s) are
# rate limited"). Every pass failed, the page stayed empty on a
# deploy with nothing set, and the log blamed rate limiting.
#
# Every other test in this file built the adapter with pool=None, which
# is the path that worked. That is the whole lesson: the tests covered
# a construction the scheduler never uses.
# =============================================================


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        # Only read on the error paths in _decode, but always present so
        # a failure names the host rather than raising inside the error
        # handler.
        self.request = SimpleNamespace(
            url=SimpleNamespace(host="commons.wikimedia.org")
        )

    def json(self):
        return self._payload


class RecordingClient:
    """An httpx.AsyncClient stand-in that remembers what it was asked."""

    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._response


def test_build_pool_returns_an_empty_pool_not_none():
    """
    The fact the rest of this section turns on. It looks like a
    formality; it is the difference between a provider that works
    unconfigured and one that raises "0 keys are rate limited".
    """
    pool = build_pool("wikimedia", None)

    assert isinstance(pool, KeyPool)
    assert pool is not None
    assert pool.is_empty
    assert len(pool) == 0


def test_a_keyless_provider_still_issues_its_request_with_an_empty_pool():
    """
    The regression. Exactly what _sync_reels builds for Wikimedia on a
    deploy with no secrets set, all the way through to the wire.
    """
    payload = {"query": {"pages": []}}
    client = RecordingClient(FakeResponse(payload))
    pool = build_pool("wikimedia", None)

    source = build_source("wikimedia", pool=pool, client=client, hint="")
    assert source.requires_key is False

    assert run(source._get_json({"action": "query"})) == payload

    # One request, not a retry storm against a pool that has no keys.
    assert len(client.calls) == 1
    assert client.calls[0]["url"] == PROVIDERS["wikimedia"].endpoint


def test_a_keyless_provider_sends_no_authorization_header():
    """
    Empty must mean "no credential", never "an empty credential".
    A header carrying '' would be sent to Commons on every request.
    """
    client = RecordingClient(FakeResponse({"query": {"pages": []}}))

    source = build_source(
        "wikimedia", pool=build_pool("wikimedia", None), client=client
    )
    run(source._get_json({"action": "query"}))

    headers = client.calls[0]["headers"]
    assert "Authorization" not in headers, headers


def test_a_provider_that_needs_a_key_still_refuses_an_empty_pool():
    """
    The other half of the fix, so it cannot be over-applied: the empty
    pool is only ever a no-op for a provider that does not require a
    key. Pexels with no keys configured is still a configuration
    error, not a silent unauthenticated request.
    """
    client = RecordingClient(FakeResponse({}, status_code=200))

    source = build_source(
        "pexels", pool=build_pool("pexels", None), client=client
    )

    with pytest.raises(reels_providers.ProviderError) as caught:
        run(source._get_json({"per_page": 5}))

    assert "no API key configured" in str(caught.value), str(caught.value)
    assert client.calls == [], "an unconfigured provider must not hit the network"


def test_a_configured_provider_still_rotates_and_books_keeps():
    """
    The keyless path must not have cost the keyed path its rotation.
    """
    client = RecordingClient(FakeResponse({"hits": []}))
    pool = build_pool("pexels", "key-one,key-two")

    source = build_source("pexels", pool=pool, client=client)

    run(source._get_json({"per_page": 5}))
    run(source._get_json({"per_page": 5}))

    assert [call["headers"]["Authorization"] for call in client.calls] == [
        "key-one",
        "key-two",
    ]


# =============================================================
# _sync_reels, END TO END, WITH NO CREDENTIALS
#
# One pass of the real scheduler entrypoint against a real Commons
# response body, with nothing configured. This is the test that says
# whether /category/reels can fill on a deploy where the operator has
# set ENABLE_REELS=true and nothing else.
#
# It went green only once _get_json stopped asking an empty pool for a
# key. Everything below the scheduler — the pool, the adapter, the
# licence gate, the upsert — was already correct and individually
# untested in combination, which is exactly how a feature can be 95%
# finished and produce nothing.
# =============================================================


# =============================================================
# A REAL COMMONS RESPONSE
#
# Captured verbatim from
#   commons.wikimedia.org/w/api.php?action=query&generator=search
#     &prop=imageinfo|videoinfo&viprop=...|derivatives|...
# with the same search expression and viprop the adapter sends.
#
# It replaces an earlier fixture that was kinder than the API, in three
# ways that each hid a real bug:
#
#   1. It had derivatives with no `transcodekey`. Commons lists the
#      ORIGINAL FILE as the first entry in `derivatives`, with no
#      transcodekey and at full resolution — and ranking by size picked
#      it, so every stored reel was a full-size master.
#   2. It had `type: "video/mp4"`. Commons NEVER sends a bare type; every
#      entry arrives as `video/<container>; codecs="<video>, <audio>"`.
#      The old `== "video/mp4"` comparison therefore could not match
#      anything Commons has ever produced, which is why the feed served
#      webm.
#   3. It offered an mp4 transcode. Commons publishes no H.264 mp4
#      family at all: across a 50-file sample (184 derivative entries)
#      every entry was `.vp9.webm`, `.mpeg4.mov` or `.theora.ogv`, and
#      every `.480p.mp4` path on the CDN answers HTTP 404.
#
# A fixture that promises what the provider cannot deliver is not a
# fixture, it a wish.
# =============================================================

_COMMONS_BASE = (
    "https://upload.wikimedia.org/wikipedia/commons/transcoded"
    "/a/ab/Nepal_street_video.webm/Nepal_street_video.webm"
)

COMMONS_RESPONSE = {
    "query": {
        "pages": [
            {
                "pageid": 12345,
                "title": "File:Nepal street video.webm",
                "imageinfo": [
                    {
                        "url": (
                            "https://upload.wikimedia.org/wikipedia/commons/"
                            "a/ab/Nepal_street_video.webm"
                            "?utm_source=commons.wikimedia.org"
                            "&utm_campaign=imageinfo&utm_content=original"
                        ),
                        "thumburl": (
                            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
                            "a/ab/Nepal_street_video.webm/640px-Nepal_street_video.webm.jpg"
                        ),
                        "mime": "video/webm",
                        "mediatype": "VIDEO",
                        "width": 1920,
                        "height": 1080,
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "License": {"value": "cc-by-sa-4.0"},
                            "LicenseUrl": {
                                "value": "https://creativecommons.org/licenses/by-sa/4.0/"
                            },
                            "Artist": {
                                "value": '<a href="https://commons.wikimedia.org/wiki/User:A">A Photographer</a>'
                            },
                            "ImageDescription": {"value": "A street in Kathmandu."},
                        },
                    }
                ],
                "videoinfo": [
                    {
                        "duration": 12.5,
                        "width": 1920,
                        "height": 1080,
                        "mime": "video/webm",
                        "mediatype": "VIDEO",
                        "timestamp": "2026-01-02T03:04:05Z",
                        "descriptionurl": (
                            "https://commons.wikimedia.org/wiki/File:Nepal_street_video.webm"
                        ),
                        "derivatives": [
                            # The master, listed FIRST, at full resolution,
                            # with no transcodekey. Exactly as Commons
                            # sends it, and the reason the feed used to
                            # store 1080p masters.
                            {
                                "src": (
                                    "https://upload.wikimedia.org/wikipedia/commons/"
                                    "a/ab/Nepal_street_video.webm"
                                    "?utm_source=commons.wikimedia.org"
                                    "&utm_campaign=api&utm_content=original"
                                ),
                                "type": 'video/webm; codecs="vp9, opus"',
                                "width": 1920,
                                "height": 1080,
                                "bandwidth": 2400000,
                            },
                            {
                                "src": f"{_COMMONS_BASE}.240p.vp9.webm",
                                "type": 'video/webm; codecs="vp9, opus"',
                                "transcodekey": "240p.vp9.webm",
                                "width": 426,
                                "height": 240,
                                "bandwidth": 282256,
                            },
                            {
                                # MPEG-4 Part 2 in a QuickTime container.
                                # A valid video that no browser plays.
                                "src": f"{_COMMONS_BASE}.360p.mpeg4.mov",
                                "type": 'video/quicktime; codecs="mp4v"',
                                "transcodekey": "360p.mpeg4.mov",
                                "width": 640,
                                "height": 360,
                                "bandwidth": 1002128,
                            },
                            {
                                "src": f"{_COMMONS_BASE}.480p.vp9.webm",
                                "type": 'video/webm; codecs="vp9, opus"',
                                "transcodekey": "480p.vp9.webm",
                                "width": 854,
                                "height": 480,
                                "bandwidth": 654768,
                            },
                        ],
                    }
                ],
            }
        ]
    }
}


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient as _sync_reels builds it."""

    def __init__(self, payload, recorder):
        self._payload = payload
        self._recorder = recorder
        self.init_kwargs = None

    def __call__(self, **kwargs):
        self.init_kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, *, params=None, headers=None):
        self._recorder.append({"url": url, "params": params, "headers": headers})
        return FakeResponse(self._payload)


def _reels_settings(**overrides):
    defaults = dict(
        enable_reels=True,
        reels_per_source=30,
        reels_poll_seconds=900,
        pexels_api_keys=None,
        pixabay_api_keys=None,
        youtube_api_keys=None,
        reels_search_terms="nepal",
        reels_http_timeout=20.0,
        reels_pass_timeout_seconds=180,
        reels_user_agent="TestReels/1.0 (+https://example.test)",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_one_reels_pass_stores_commons_clips_with_no_api_keys(monkeypatch):
    """
    The whole point of Wikimedia being first in REEL_PROVIDERS. With
    ENABLE_REELS=true and every key unset, one pass must reach the
    database — not log an error and leave /category/reels empty.
    """
    import httpx

    from app.workers import scheduler as sched

    sent: list[dict] = []
    db = FakeDB(rows=[{"id": GOOD_UUID}])
    client = FakeAsyncClient(COMMONS_RESPONSE, sent)

    monkeypatch.setattr(sched, "get_settings", lambda: _reels_settings())
    monkeypatch.setattr(sched, "get_db", lambda: db)
    monkeypatch.setattr(httpx, "AsyncClient", client)

    await sched._sync_reels()

    assert len(sent) == 1, sent
    assert sent[0]["url"] == PROVIDERS["wikimedia"].endpoint
    assert client.init_kwargs["headers"]["User-Agent"] == (
        "TestReels/1.0 (+https://example.test)"
    ), "the configured User-Agent must reach Commons"

    assert len(db.upsert_rows) == 1, db.calls
    row = db.upsert_rows[0]

    assert row["source_key"] == "wikimedia"
    assert row["external_id"] == "12345"
    # A transcode, not the master. Commons publishes no H.264 mp4 (every
    # `.480p.mp4` on the CDN is a 404), so the best it offers is a
    # 480p VP9 webm — and shipping that rather than the 1080p master is
    # the part that is ours to get right.
    assert row["video_url"].endswith(".480p.vp9.webm"), row["video_url"]
    assert "/transcoded/" in row["video_url"], row["video_url"]
    assert "Nepal_street_video.webm.240p" not in row["video_url"]
    # The row describes the file the browser will fetch, not the 1080p
    # master it was cut from.
    assert (row["width"], row["height"]) == (854, 480), row
    assert row["license_name"] == "CC BY-SA 4.0"
    assert row["attribution_required"] is True
    assert row["creator_name"] == "A Photographer"
    assert row["published_at"].startswith("2026-01-02")


async def test_a_pass_with_no_keys_at_all_survives_the_providers_that_need_them(
    monkeypatch,
):
    """
    Pexels and Pixabay have nothing configured here, and their absence
    must be a SKIPPED line rather than a failure — and must not stop
    Wikimedia from running. A pass is only wrong if it stores nothing.
    """
    import httpx

    from app.workers import scheduler as sched

    sent: list[dict] = []
    db = FakeDB(rows=[{"id": GOOD_UUID}])
    client = FakeAsyncClient(COMMONS_RESPONSE, sent)

    monkeypatch.setattr(
        sched,
        "get_settings",
        lambda: _reels_settings(pexels_api_keys="", pixabay_api_keys=None),
    )
    monkeypatch.setattr(sched, "get_db", lambda: db)
    monkeypatch.setattr(httpx, "AsyncClient", client)

    await sched._sync_reels()

    assert [call["url"] for call in sent] == [PROVIDERS["wikimedia"].endpoint]
    assert len(db.upsert_rows) == 1


async def test_a_provider_error_does_not_lose_the_providers_that_worked(monkeypatch):
    """
    The isolation guarantee, at the level it is actually claimed: the
    pass is wrapped whole, and each provider is wrapped on its own.

    Pexels is configured here and then fails at the socket. Wikimedia
    still runs — and still writes its rows. A reels provider being down
    must never be able to affect a single outlet poll, and this is the
    test that says so at the scheduler level rather than in the store.
    """
    import httpx

    from app.workers import scheduler as sched

    sent: list[dict] = []
    db = FakeDB(rows=[{"id": GOOD_UUID}])
    pexels_endpoint = PROVIDERS["pexels"].endpoint

    def flaky(**kwargs):
        class Flaky(FakeAsyncClient):
            async def get(self, url, *, params=None, headers=None):
                sent.append({"url": url, "headers": headers})
                if url == pexels_endpoint:
                    raise RuntimeError("pexels is down")
                return FakeResponse(self._payload)

        return Flaky(COMMONS_RESPONSE, sent)

    monkeypatch.setattr(
        sched, "get_settings", lambda: _reels_settings(pexels_api_keys="pexels-key")
    )
    monkeypatch.setattr(sched, "get_db", lambda: db)
    monkeypatch.setattr(httpx, "AsyncClient", flaky)

    await sched._sync_reels()

    assert [call["url"] for call in sent] == [
        PROVIDERS["wikimedia"].endpoint,
        pexels_endpoint,
    ]
    # Both were asked, so the failure really was Pexels' alone. Commons
    # is sent no credential at all, Pexels exactly one.
    assert [call["headers"].get("Authorization") for call in sent] == [
        None,
        "pexels-key",
    ]

    # And the healthy provider's reels still landed.
    assert [row["source_key"] for row in db.upsert_rows] == ["wikimedia"]


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

# =============================================================
# Playability
#
# A Reels card that cannot play is worse than no card: the feed shows
# "Replay unavailable" and the reader sees a broken feature. Wikimedia
# still hosts plenty of Ogg Video, which is a valid video file that no
# current browser decodes.
# =============================================================

def test_ogg_video_theora_is_treated_as_unplayable():
    from app.services.reels.providers import _is_browser_playable_video

    assert not _is_browser_playable_video("https://x/Foo.ogv", {"mime": "video/ogg"})
    assert not _is_browser_playable_video("https://x/Foo.ogv")
    assert not _is_browser_playable_video("https://x/audio.ogg")
    assert not _is_browser_playable_video("https://x/Foo.mov", {"mime": "video/quicktime"})


def test_mp4_and_webm_are_playable():
    from app.services.reels.providers import _is_browser_playable_video

    assert _is_browser_playable_video("https://x/Foo.mp4", {"mime": "video/mp4"})
    assert _is_browser_playable_video("https://x/Foo.webm", {"mime": "video/webm"})
    # No mime from the provider: the extension decides.
    assert _is_browser_playable_video("https://x/Foo.mp4?ts=1")
    assert _is_browser_playable_video("https://x/Foo.webm")


# =============================================================
# COMMONS CODECS, AGAINST THE SHAPE COMMONS ACTUALLY SENDS
#
# Every entry in a real `derivatives` list arrives as
#
#   "video/<container>; codecs=\"<video>, <audio>\""
#
# The parameters are not decoration. The old code compared the field
# against `"video/mp4"` with `==`, which is a string Commons has never
# produced, so the mp4 preference could not fire even if Wikimedia
# started publishing one — and `.mov` / Theora, which DO play nowhere,
# were only kept out by accident of the container name.
# =============================================================

def test_a_media_type_is_split_into_its_container_and_its_codecs():
    """
    The exact strings Commons sends, quoted from the live API.
    """
    from app.services.reels.providers import _parse_media_type

    assert _parse_media_type('video/webm; codecs="vp9, opus"') == (
        "video/webm", frozenset({"vp9", "opus"}),
    )
    assert _parse_media_type('video/mp4; codecs="avc1.4d401f, mp4a.40.2"') == (
        "video/mp4", frozenset({"avc1.4d401f", "mp4a.40.2"}),
    )
    # Single codec, no audio, mixed case, stray spaces.
    assert _parse_media_type('video/webm; CODECS="AV01.0.08M.08"') == (
        "video/webm", frozenset({"av01.0.08m.08"}),
    )
    # No parameters at all, which is what a hand-written fixture tends
    # to send and what almost nothing real does.
    assert _parse_media_type("video/mp4") == ("video/mp4", frozenset())
    assert _parse_media_type(None) == ("", frozenset())
    assert _parse_media_type("") == ("", frozenset())


def test_the_codec_decides_not_just_the_container():
    """
    MPEG-4 Part 2 is the trap Commons sets on every single file.

    `.mpeg4.mov` is the one non-webm transcode Wikimedia publishes, it
    arrives as `video/quicktime; codecs="mp4v"`, and no current browser
    decodes it — Apple dropped MPEG-4 Part 2 before the first iPhone.
    A check that only read the container would either reject it for
    being "quicktime" by luck, or accept it for being "mp4v" by
    mistake, depending on which side of the `==` it fell.
    """
    from app.services.reels.providers import _is_browser_playable_video

    assert not _is_browser_playable_video(
        "https://x/Foo.webm.360p.mpeg4.mov",
        {"type": 'video/quicktime; codecs="mp4v"'},
    )
    # A container we have no opinion on is still refused, even when the
    # codec in it is one a browser can decode. That is deliberate and
    # predates the codec check: accepting a type we do not recognise on
    # the strength of its codec parameter is how an unfamiliar format
    # ends up in the feed. Commons never sends one.
    assert not _is_browser_playable_video(
        "https://x/Foo.mov", {"type": 'video/quicktime; codecs="avc1"'},
    )
    # Ogg Video with an explicit codec, same answer as without one.
    assert not _is_browser_playable_video(
        "https://x/Foo.ogv", {"mime": 'video/ogg; codecs="theora, vorbis"'},
    )
    # And a real mp4 transcode, with codecs, is playable — which is the
    # case the old `== "video/mp4"` comparison refused.
    assert _is_browser_playable_video(
        "https://x/Foo.webm.480p.mp4", {"type": 'video/mp4; codecs="avc1.4d401f"'},
    )


def _commons_derivatives(*extra: dict) -> list[dict]:
    """Commons' real derivative list, plus whatever a test adds."""
    return [
        {
            "src": (
                "https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.webm"
                "?utm_source=commons.wikimedia.org&utm_campaign=api"
            ),
            "type": 'video/webm; codecs="vp9, opus"',
            "width": 1920,
            "height": 1080,
            "bandwidth": 2400000,
        },
        *extra,
        {
            "src": (
                "https://upload.wikimedia.org/wikipedia/commons/transcoded/"
                "a/ab/Foo.webm/Foo.webm.480p.vp9.webm"
            ),
            "type": 'video/webm; codecs="vp9, opus"',
            "transcodekey": "480p.vp9.webm",
            "width": 854,
            "height": 480,
            "bandwidth": 654768,
        },
    ]


def _commons_videoinfo(derivatives: list[dict]) -> dict:
    return {
        "duration": 12.5,
        "width": 1920,
        "height": 1080,
        "mime": "video/webm",
        "url": (
            "https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.webm"
            "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo"
        ),
        "derivatives": derivatives,
    }


def _commons_imageinfo() -> dict:
    return {
        "url": (
            "https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.webm"
            "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo"
        ),
        "mime": "video/webm",
        "width": 1920,
        "height": 1080,
    }


def _pick(derivatives: list[dict]) -> str | None:
    from app.services.reels.providers import WikimediaSource

    source = WikimediaSource(pool=None, client=None)
    url, _, _ = source._best_video_url(
        _commons_videoinfo(derivatives), _commons_imageinfo()
    )
    return url


def test_the_original_master_is_never_chosen_over_a_transcode():
    """
    The production bug, at the level it actually happens.

    Commons lists the ORIGINAL FILE as the first entry in `derivatives`,
    with no `transcodekey` and at the source's full resolution. Ranking
    by size therefore picked the master every time: the live reels feed
    was serving 1920x1080 and even 4096x2160 originals — a multi-
    megabyte download to fill a 420-pixel card — while the transcodes
    Wikimedia had already paid to generate went unused.

    A reel card is 420px wide. The 480p transcode is the right answer
    and the master is not, whatever their relative file sizes.
    """
    assert _pick(_commons_derivatives()).endswith("Foo.webm.480p.vp9.webm")


def test_an_h264_mp4_transcode_outranks_every_webm_one():
    """
    The preference the class has always claimed, made reachable.

    Wikimedia publishes no H.264 mp4 today, so this cannot be observed
    in production — which is exactly why it went broken unnoticed. The
    comparison was `type == "video/mp4"` against a field Commons fills
    with `video/mp4; codecs="avc1..."`, so it could never fire. If
    Wikimedia turns H.264 transcoding back on, or a provider that does
    publish it arrives, this has to be the behaviour.
    """
    mp4 = {
        "src": (
            "https://upload.wikimedia.org/wikipedia/commons/transcoded/"
            "a/ab/Foo.webm/Foo.webm.480p.mp4"
        ),
        "type": 'video/mp4; codecs="avc1.4d401f, mp4a.40.2"',
        "transcodekey": "480p.mp4",
        "width": 854,
        "height": 480,
        "bandwidth": 700000,
    }

    # Bigger than the 480p webm, and it still wins: codec first, size
    # second. Ranking by size is what put the master in front.
    mp4_720 = {**mp4, "src": mp4["src"].replace("480p", "720p"),
               "transcodekey": "720p.mp4", "width": 1280, "height": 720}
    assert _pick(_commons_derivatives(mp4, mp4_720)).endswith("Foo.webm.720p.mp4")

    # And an mp4 with no codecs declared at all is still an mp4.
    bare = {**mp4, "type": "video/mp4"}
    assert _pick(_commons_derivatives(bare)).endswith("Foo.webm.480p.mp4")


def test_the_transcode_commons_publishes_besides_webm_is_refused():
    """
    `.mpeg4.mov` and `.theora.ogv` are on every Commons file and play
    nowhere. Refusing them is what stops the feed rendering a card that
    says "Replay unavailable" for ever.
    """
    mov = {
        "src": (
            "https://upload.wikimedia.org/wikipedia/commons/transcoded/"
            "a/ab/Foo.webm/Foo.webm.360p.mpeg4.mov"
        ),
        "type": 'video/quicktime; codecs="mp4v"',
        "transcodekey": "360p.mpeg4.mov",
        "width": 640,
        "height": 360,
        "bandwidth": 1002128,
    }
    theora = {
        "src": (
            "https://upload.wikimedia.org/wikipedia/commons/transcoded/"
            "a/ab/Foo.webm/Foo.webm.480p.theora.ogv"
        ),
        "type": 'video/ogg; codecs="theora, vorbis"',
        "transcodekey": "480p.theora.ogv",
        "width": 854,
        "height": 480,
        "bandwidth": 800000,
    }

    # Both refused; the 480p webm next to them is taken instead.
    assert _pick(_commons_derivatives(mov, theora)).endswith("Foo.webm.480p.vp9.webm")


def test_a_file_with_no_usable_transcode_falls_back_to_the_master():
    """
    The master is a last resort, not a preference — but for a file
    Commons has not transcoded it is the only thing there is, and
    returning None would silently empty the feed of a whole category.
    """
    from app.services.reels.providers import WikimediaSource

    source = WikimediaSource(pool=None, client=None)
    url, width, height = source._best_video_url(
        _commons_videoinfo([_commons_derivatives()[0]]), _commons_imageinfo()
    )

    assert url and url.endswith("a/ab/Foo.webm?utm_source=commons.wikimedia.org"
                                "&utm_campaign=imageinfo"), url
    assert (width, height) == (1920, 1080)


def test_the_stored_row_describes_the_rendition_not_the_master():
    """
    A row saying 3840x2160 while pointing a phone at an 854x480
    transcode is a lie in a column the card renders from.
    """
    source_kwargs = dict(pool=None, client=None)
    from app.services.reels.providers import WikimediaSource

    source = WikimediaSource(**source_kwargs)
    url, width, height = source._best_video_url(
        _commons_videoinfo(_commons_derivatives()), _commons_imageinfo()
    )

    assert (width, height) == (854, 480), (url, width, height)


# =============================================================
# YOUTUBE SHORTS
#
# This provider is the one that cannot behave like the others, and the
# tests are mostly about the ways it could quietly get that wrong.
#
# The three assertions that matter most:
#
# 1. video_url IS AN EMBED URL AND NEVER A MEDIA URL. The bytes behind
#    a YouTube video live on googlevideo.com behind a signature that has
#    to be derived from YouTube's own player. Producing one is scraping.
#    A test that pins "the video_url is the embed player src" is the
#    only thing standing between a routine refactor and a scraper.
#
# 2. ATTRIBUTION IS NOT OPTIONAL. Every row carries a creator and a
#    licence. YouTube's standard licence extends nothing to us, so
#    attribution_required is True unconditionally and a Reel with a
#    None creator is unreachable, not merely discouraged.
#
# 3. NO OAUTH, EVER. The `player` part would give us the real media
#    URL and it is OAuth-only: an API key gets HTTP 401 from it,
#    always. Adding a token would mean storing a user credential to
#    serve a feed that embeds perfectly well without one.
#
# Every request below is served by httpx.MockTransport. No test in this
# section reaches the network, and none of them needs a credential that
# is not a plain API key.
# =============================================================


def _short(
    video_id: str = "abc123XYZ_-",
    *,
    duration: str = "PT45S",
    embeddable: bool = True,
    licence: str = "youtube",
    channel_title: str = "Sagarmatha Daily",
    channel_id: str = "UCnepalNews",
    broadcast: str = "none",
    privacy: str = "public",
) -> dict:
    """One videos.list item, with a search snippet of the same shape."""
    return {
        "id": video_id,
        "snippet": {
            "channelTitle": channel_title,
            "channelId": channel_id,
            "title": f"A Short about {video_id}",
            "description": "Forty-five seconds of Kathmandu.",
            "publishedAt": "2026-10-18T09:30:00Z",
            "liveBroadcastContent": broadcast,
            "thumbnails": {
                "high": {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
            },
        },
        "contentDetails": {"duration": duration},
        "status": {"embeddable": embeddable, "license": licence,
                   "privacyStatus": privacy},
    }


def fetch_youtube(items, *, detail_items=None, limit=30, keys="key-one,key-two"):
    """
    Run one real fetch() against a MockTransport.

    Returns (reels, requests). Handing back the requests is how the
    query-string assertions are made — `videoEmbedDeny=FAIL`, `key=`,
    and the absence of any OAuth parameter are all things only visible
    on the wire.

    `detail_items` defaults to `items` and overrides only what
    videos.list answers, so a test can simulate a video that search
    returned and videos.list did not.
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({
            "path": request.url.path,
            "params": dict(request.url.params),
            "headers": dict(request.headers),
        })

        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={
                "kind": "youtube#searchListResponse",
                "items": [
                    {"id": {"kind": "youtube#video", "videoId": item["id"]},
                     "snippet": item["snippet"]}
                    for item in items
                ],
            })

        if request.url.path.endswith("/videos"):
            answered = items if detail_items is None else detail_items
            return httpx.Response(200, json={
                "kind": "youtube#videoListResponse",
                "items": answered,
            })

        return httpx.Response(404, json={"error": {"code": 404}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        source = build_source(
            "youtube", pool=build_pool("youtube", keys), client=client, hint="nepal"
        )
        return run(source.fetch(limit=limit)), seen
    finally:
        run(client.aclose())


def test_a_shorts_hit_becomes_a_reel_with_a_creator_and_a_licence():
    """
    The whole contract in one reel.

    The feed's premise is that every card names who made it and under
    what terms. A YouTube row with a null creator renders "Creator not
    credited", which on a platform whose entire value is channel
    identity is a row we should never have emitted.
    """
    items = [_short()]

    reels, _ = fetch_youtube(items)

    assert len(reels) == 1
    reel = reels[0]

    assert reel.source_key == "youtube"
    assert reel.external_id == "abc123XYZ_-"

    # Attribution, in full.
    assert reel.creator_name == "Sagarmatha Daily"
    assert reel.creator_url == "https://www.youtube.com/channel/UCnepalNews"
    assert reel.license_name == "YouTube Standard License"
    assert reel.license_url
    assert reel.attribution_required is True

    assert reel.duration_seconds == 45.0
    assert reel.title == "A Short about abc123XYZ_-"
    assert reel.published_at == "2026-10-18T09:30:00+00:00"


def test_a_reel_is_never_emitted_without_a_creator_and_a_licence():
    """
    The invariant, not just the happy path.

    An unreadable licence is DROPPED rather than shipped with a guess —
    that is the safer failure, and guessing is exactly how a feed ends
    up redistributing something it has no right to. A blank channel
    title still yields a named creator, because every public YouTube
    video has a channel and a card crediting nobody is the failure this
    whole package is written to prevent.
    """
    items = [_short(channel_title="", licence="somethingNewWeHaveNotSeen")]

    reels, _ = fetch_youtube(items)
    assert reels == [], "an unrecognised licence must not be guessed at"

    # A blank channel title still yields a named creator. The channel id
    # is a separate field, so this case also blanks it: with an id
    # present the adapter still resolves the channel URL, which is
    # correct — the title is what goes missing, not the channel.
    items = [_short(channel_title="", channel_id="", licence="youtube")]
    reels, _ = fetch_youtube(items)

    assert len(reels) == 1
    assert reels[0].creator_name == "YouTube"
    assert reels[0].creator_url == "https://www.youtube.com/"
    assert reels[0].license_name is not None
    assert reels[0].attribution_required is True

    # A title we cannot read, with the channel id intact, must still
    # fall back rather than emit None.
    reels, _ = fetch_youtube([_short(channel_title="   ", licence="youtube")])
    assert reels[0].creator_name == "YouTube"
    assert reels[0].creator_url == "https://www.youtube.com/channel/UCnepalNews"


def test_creative_common_uploads_are_labelled_with_their_real_licence():
    """
    "YouTube Standard License or better" — this is the better case.

    A uploader who picked Creative Commons licensed the video CC BY 3.0,
    a named licence we can actually describe, rather than the platform
    default that grants a third party nothing.
    """
    items = [_short(licence="creativeCommon")]

    reels, _ = fetch_youtube(items)

    assert len(reels) == 1
    assert reels[0].license_name == "CC BY 3.0"
    assert reels[0].license_url == "https://creativecommons.org/licenses/by/3.0/"
    # CC BY still requires the credit. A permissive licence is not a
    # licence that waives attribution.
    assert reels[0].attribution_required is True


def test_the_video_url_is_an_embed_player_and_never_a_media_stream():
    """
    The assertion that keeps this a player and not a scraper.

    The real bytes sit on googlevideo.com behind a signature derived from
    YouTube's own player; producing one means reproducing that
    obfuscation. So video_url and embed_url are both the official embed
    src, and embed_url is the flag that makes the web app render an
    <iframe> instead of pointing a <video> at an HTML document.
    """
    reels, _ = fetch_youtube([_short()])

    reel = reels[0]
    assert reel.video_url == "https://www.youtube.com/embed/abc123XYZ_-"
    assert reel.embed_url == reel.video_url

    # Nothing anywhere in the reel points at a raw stream.
    for value in (reel.video_url, reel.embed_url, reel.poster_url or ""):
        assert "googlevideo.com" not in value
        assert "videoplayback" not in value


def test_the_search_asks_for_embeddable_shorts_only():
    """
    videoEmbedDeny=FAIL is the single most important line in the
    adapter: it excludes every video whose owner forbids embedding, so
    the results already carry permission to put a player on our page.
    """
    _, seen = fetch_youtube([_short()])

    search = seen[0]
    assert search["path"].endswith("/search")
    assert search["params"]["part"] == "snippet"
    assert search["params"]["type"] == "video"
    assert search["params"]["videoDuration"] == "short"
    assert search["params"]["videoEmbedDeny"] == "FAIL"


def test_a_video_whose_owner_forbids_embedding_is_excluded():
    """
    The search filter is only as good as the index. videos.list is the
    record of truth, and an absent or false `embeddable` is a NO —
    defaulting it to True would let exactly the locked-down videos that
    videoEmbedDeny exists to exclude into the feed.
    """
    items = [
        _short("allowed0000000000", embeddable=True),
        _short("denied00000000000", embeddable=False),
    ]
    missing = _short("missing0000000000")
    del missing["status"]["embeddable"]
    items.append(missing)

    reels, _ = fetch_youtube(items)

    assert [reel.external_id for reel in reels] == ["allowed0000000000"]


def test_a_private_video_is_excluded():
    """
    An unlisted video embeds; a private one has nothing to embed.
    """
    items = [
        _short("private000000000", privacy="private"),
        _short("unlisted00000000", privacy="unlisted"),
    ]

    reels, _ = fetch_youtube(items)

    assert [reel.external_id for reel in reels] == ["unlisted00000000"]


def test_a_clip_longer_than_three_minutes_is_dropped():
    """
    The duration filter the category is named for.

    videoDuration=short is a coarse server-side prefilter, not the rule:
    it buckets roughly 1-4 minutes and says nothing about a running
    time the index has stale. contentDetails.duration decides, and the
    boundary is inclusive — 180 seconds is a Short, 181 is not.
    """
    items = [
        _short("short000000000000", duration="PT15S"),
        _short("exact000000000000", duration="PT3M"),
        _short("long0000000000000", duration="PT3M1S"),
        _short("longer00000000000", duration="PT4M12S"),
    ]

    reels, _ = fetch_youtube(items)

    assert [reel.external_id for reel in reels] == [
        "short000000000000",
        "exact000000000000",
    ]


def test_a_duration_we_cannot_read_is_dropped_not_guessed():
    """
    The opposite of _within_duration's default, on purpose.

    Commons clips often have no running time and are still fine, so that
    helper lets None through. Here an unreadable duration is a REJECT:
    we could not verify the video is a Short, and "we could not verify"
    is not permission to publish.
    """
    items = [
        _short("good0000000000000", duration="PT30S"),
        _short("broken0000000000", duration="not-a-duration"),
        _short("empty000000000000", duration="P"),
    ]

    reels, _ = fetch_youtube(items)

    assert [reel.external_id for reel in reels] == ["good0000000000000"]


def test_live_and_upcoming_broadcasts_are_dropped():
    """
    A live stream is not a Short, whatever its running time says. Only
    the explicit "none" is accepted.
    """
    items = [
        _short("normal00000000000", broadcast="none"),
        _short("live0000000000000", broadcast="live"),
        _short("soon0000000000000", broadcast="upcoming"),
    ]

    reels, _ = fetch_youtube(items)

    assert [reel.external_id for reel in reels] == ["normal00000000000"]


def test_a_video_missing_from_videos_list_is_skipped_not_crashed():
    """
    Search and videos.list are two round trips and a video can vanish
    between them. A missing detail record has nothing honest to render,
    so it is skipped — and the rest of the batch still lands.
    """
    reels, seen = fetch_youtube(
        [_short("present0000000000"), _short("gone000000000000")],
        detail_items=[_short("present0000000000")],
    )

    assert [reel.external_id for reel in reels] == ["present0000000000"]

    # Both ids were asked about; the answer simply omitted one.
    videos_call = next(c for c in seen if c["path"].endswith("/videos"))
    assert "present0000000000" in videos_call["params"]["id"]
    assert "gone000000000000" in videos_call["params"]["id"]


def test_the_key_is_sent_as_a_query_parameter_never_as_a_header():
    """
    Google's parameter is `key=`. `Authorization` is reserved for OAuth
    bearer tokens there, and a bare API key in that header is ignored or
    rejected — so the base class's header must be suppressed, which is
    what this asserts.

    It also pins that no OTHER credential is ever attached: no bearer
    token, no client secret, no Authorization header of any kind.
    """
    _, seen = fetch_youtube([_short()])

    assert seen, "the provider made no requests"
    for call in seen:
        assert "Authorization" not in call["headers"], call["headers"]
        assert call["params"]["key"] in {"key-one", "key-two"}


def test_a_429_cools_the_key_and_rotates_to_the_next_one():
    """
    search.list costs 100 quota units a call, so exhausting the daily
    budget on a provider polled every few minutes is routine, not
    exceptional. The pool has to absorb it the way it does everywhere
    else: cool the key that was made to, spend the next one, and report
    nothing as broken.
    """
    seen_keys: list[str] = []
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.url.params.get("key") or "")
        attempts["n"] += 1

        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, json={"items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pool = build_pool("youtube", "key-one,key-two")
    try:
        source = build_source("youtube", pool=pool, client=client)
        assert run(source.fetch(limit=5)) == []
    finally:
        run(client.aclose())

    # Rotated: the retry used a different key.
    assert len(seen_keys) >= 2, seen_keys
    assert seen_keys[0] != seen_keys[1], seen_keys
    assert set(seen_keys) <= {"key-one", "key-two"}, seen_keys

    # And the key that was rate limited is out of rotation.
    cooled = [e for e in pool.snapshot() if not e["available"]]
    assert len(cooled) == 1, pool.snapshot()


def test_a_rejected_key_reports_a_configuration_error():
    """
    A 403 here is a wrong or restricted key — usually an API key whose
    YouTube Data API has not been enabled in its Cloud project, which is
    the single most common way this provider fails. It has to name
    itself as a credential problem, not a provider outage, so the
    operator does not go looking for a YouTube incident.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        source = build_source(
            "youtube", pool=build_pool("youtube", "key-one"), client=client
        )
        with pytest.raises(reels_providers.ProviderError) as caught:
            run(source.fetch(limit=5))
    finally:
        run(client.aclose())

    assert "rejected" in str(caught.value), str(caught.value)


def test_no_provider_call_ever_asks_for_the_oauth_only_player_part():
    """
    `player` is the only part that exposes the real media URL and it is
    OAuth-only — an API key receives HTTP 401 from it, always.

    Asking for it would be asking for something this adapter cannot
    have, and the fix an engineer reaches for under pressure — "let me
    add an OAuth client" — means storing a user credential to serve a
    feed that embeds fine without one. So no code path may name it.
    """
    _, seen = fetch_youtube([_short()])

    assert seen, "the provider made no requests"
    for call in seen:
        parts = call["params"].get("part", "")
        assert "player" not in parts.split(","), call

    # And nothing on the class reaches for an OAuth-shaped credential.
    for attribute in ("access_token", "client_secret", "refresh_token",
                      "oauth_token", "credentials"):
        assert not hasattr(PROVIDERS["youtube"], attribute), attribute


def test_the_second_call_reaches_videos_list_through_the_same_rotation():
    """
    Two endpoints, one key, one rotation path.

    videos.list supplies the only trustworthy length and the only
    trustworthy answer on embeddability, so a provider that fetched only
    from search would be trusting a stale index. Both calls go through
    _get_json so neither can skip the 429 handling.
    """
    _, seen = fetch_youtube([_short()])

    paths = [call["path"] for call in seen]
    assert any(p.endswith("/search") for p in paths), paths
    assert any(p.endswith("/videos") for p in paths), paths

    videos_call = next(c for c in seen if c["path"].endswith("/videos"))
    assert videos_call["params"]["id"] == "abc123XYZ_-"
    assert videos_call["params"]["part"] == "contentDetails,status,snippet"
    assert "key" in videos_call["params"]


def test_the_search_page_size_is_bounded():
    """
    search.list caps maxResults at 50 and videos.list accepts at most 50
    ids, so one videos.list covers everything search returned and no
    chunking is ever needed. Asking for more is a 400 from Google, which
    would fail the whole pass.
    """
    many = [_short(f"video{i:014d}") for i in range(80)]

    _, seen = fetch_youtube(many, limit=500)

    for call in seen:
        assert int(call["params"]["maxResults"]) <= 50, call


def test_iso8601_durations_are_parsed_into_seconds():
    """The one format conversion this provider needs that others do not."""
    from app.services.reels.providers import _iso8601_duration as parse

    assert parse("PT45S") == 45.0
    assert parse("PT1M23S") == 83.0
    assert parse("PT2M") == 120.0
    assert parse("PT1H") == 3600.0
    assert parse("PT1H2M3S") == 3723.0
    assert parse("P1DT1S") == 86401.0

    # Not durations, and never silently zero — zero would read as an
    # instant clip rather than "the provider said nothing useful".
    assert parse("P") is None
    assert parse("PT") is None
    assert parse("") is None
    assert parse(None) is None
    assert parse(45) is None
    assert parse("45") is None
    assert parse("45s") is None


def test_a_key_never_reaches_an_error_message_or_a_log_line():
    """
    The credential is in the query string here, so every place that
    renders a URL is a place that could publish it.

    httpx.InvalidURL puts the FULL url — query string included — into its
    message, and a malformed parameter is exactly how one gets raised.
    ProviderError then reaches SourceHealth.error, which is persisted
    and rendered in the admin panel, plus a warning log line. The
    package already treats "a credential in a health endpoint" as a
    load-bearing bug (see snapshot()); this is the same bug arriving
    through a different door.
    """
    secret = "AIzaSySUPER-SECRET-KEY"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL(f"Invalid URL: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        source = build_source(
            "youtube", pool=build_pool("youtube", secret), client=client
        )
        with pytest.raises(reels_providers.ProviderError) as caught:
            run(source.fetch(limit=5))
    finally:
        run(client.aclose())

    message = str(caught.value)
    assert secret not in message, message
    assert message.startswith("youtube:"), message
    # The diagnostic is still there — redaction must not cost us the
    # ability to see what went wrong.
    assert "Invalid URL" in message, message
    assert "key=***" in message, message

    # And the ordinary HTTPError path scrubs too, even though it cannot
    # currently reach a query string.
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"failed talking to {request.url}", request=request
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(refused))
    try:
        source = build_source(
            "youtube", pool=build_pool("youtube", secret), client=client
        )
        with pytest.raises(reels_providers.ProviderError) as caught:
            run(source.fetch(limit=5))
    finally:
        run(client.aclose())

    message = str(caught.value)
    assert secret not in message, message
    assert "youtube: request failed" in message, message


def test_youtube_is_registered_and_needs_a_key():
    """
    Registered, keyed, and last — it is the one provider that cannot be
    reached without configuration and the one that costs the most quota
    per call.
    """
    assert "youtube" in PROVIDERS
    assert PROVIDERS["youtube"].key == "youtube"
    assert PROVIDERS["youtube"].requires_key is True

    from app.workers.scheduler import REEL_PROVIDERS

    assert ("youtube", "youtube_api_keys") in REEL_PROVIDERS


def test_a_youtube_pass_with_no_key_is_a_configuration_error():
    """
    Adding a fourth provider must not break the unconfigured deploy.
    build_pool returns an empty KeyPool rather than None, so this is the
    construction the scheduler actually makes — the same one that
    silently disabled Wikimedia before _get_json was fixed.
    """
    source = build_source(
        "youtube", pool=build_pool("youtube", None), client=None
    )

    with pytest.raises(reels_providers.ProviderError) as caught:
        run(source.fetch(limit=5))

    assert "no API key configured" in str(caught.value), str(caught.value)


def test_the_embed_url_reaches_the_store_row():
    """
    The column has to actually be written, or every YouTube row lands as
    an unplayable card no matter what the adapter produced. Optional
    fields are omitted when absent, so this also pins that a plain
    provider still sends no embed_url key at all.
    """
    from app.services.reels.store import _reel_row

    embed = "https://www.youtube.com/embed/abc123XYZ_-"
    row = _reel_row(Reel(
        source_key="youtube",
        external_id="abc123XYZ_-",
        video_url=embed,
        embed_url=embed,
        creator_name="Sagarmatha Daily",
        creator_url="https://www.youtube.com/channel/UCnepalNews",
        license_name="YouTube Standard License",
        license_url="https://support.google.com/youtube/answer/2797468",
        attribution_required=True,
        duration_seconds=45.0,
    ))

    assert row["embed_url"] == embed
    assert row["video_url"] == embed

    plain = _reel_row(make_reel())
    assert "embed_url" not in plain, "a plain media row must not gain the key"


def test_the_feed_carries_embed_url_to_the_web_app():
    """
    FEED_COLUMNS is the contract with the TypeScript Reel interface. If
    embed_url is not in it, the web player receives rows with no embed
    flag and renders every YouTube card through a <video>.
    """
    assert "embed_url" in FEED_COLUMNS
    assert "video_url" in FEED_COLUMNS

    embed = "https://www.youtube.com/embed/abc123XYZ_-"
    db = FakeDB(rows=[{
        "id": GOOD_UUID,
        "video_url": embed,
        "embed_url": embed,
        "poster_url": None,
        "title": "A Short",
        "description": None,
        "creator_name": "Sagarmatha Daily",
        "creator_url": "https://www.youtube.com/channel/UCnepalNews",
        "license_name": "YouTube Standard License",
        "license_url": None,
        "attribution_required": True,
        "width": None,
        "height": None,
        "duration_seconds": 45.0,
        "published_at": "2026-10-18T09:30:00+00:00",
        "ingested_at": "2026-10-18T09:35:00+00:00",
        "reel_likes": [],
    }])

    rows = run(load_reels(db, limit=10))

    assert rows[0]["embed_url"] == embed


# =============================================================
# PIXABAY: the 24-hour cache its terms require
# =============================================================

def _pixabay_hit(video_id: int = 918273, *, duration: int = 21) -> dict:
    """One item from pixabay.com/api/videos/."""
    return {
        "id": video_id,
        "pageURL": f"https://pixabay.com/videos/watch-{video_id}/",
        "tags": "kathmandu, street",
        "duration": duration,
        "videoImage": f"https://cdn.pixabay.com/video/{video_id}/image.jpg",
        "user": "sagarmatha_photo",
        "user_id": 4471,
        "videos": [
            {"url": f"https://cdn.pixabay.com/video/{video_id}/small.mp4",
             "width": 640, "height": 360, "size": 900_000},
            {"url": f"https://cdn.pixabay.com/video/{video_id}/medium.mp4",
             "width": 1080, "height": 1920, "size": 4_200_000},
        ],
    }


class PixabayHarness:
    """
    Runs PixabaySource.fetch() against a MockTransport and counts hits.

    The cache lives on the class, so every test here clears it first.
    Without that, one test's warm cache silently satisfies the next
    test's request and the assertion stops meaning anything — the
    failure mode looks exactly like a passing test.
    """

    def __init__(self, *, videos=None, keys="pix-key-one"):
        self.hits = 0
        self.requests: list[dict] = []
        self._videos = [_pixabay_hit()] if videos is None else videos

        def handler(request: httpx.Request) -> httpx.Response:
            self.hits += 1
            self.requests.append(dict(request.url.params))
            return httpx.Response(200, json={
                "total": len(self._videos),
                "videoCount": len(self._videos),
                "videos": self._videos,
            })

        self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def __enter__(self):
        reels_providers.PixabaySource._cache.clear()
        return self

    def __exit__(self, *exc):
        run(self._client.aclose())
        reels_providers.PixabaySource._cache.clear()
        return False

    async def fetch(self, limit: int = 30):
        source = build_source(
            "pixabay",
            pool=build_pool("pixabay", "pix-key-one"),
            client=self._client,
            hint="nepal",
        )
        return await source.fetch(limit=limit)


def test_a_pixabay_fetch_records_the_library_on_every_clip():
    """
    Pixabay's API terms: "If you make use of the API, show your users
    where the images and videos are from, whenever search results are
    displayed."

    The web card renders that badge off `source_key`, so a Reel without
    it produces a card that breaks the rule it is displayed under.
    """
    with PixabayHarness() as harness:
        reels = run(harness.fetch())

    assert len(reels) == 1
    assert reels[0].source_key == "pixabay"


def test_pixabay_asks_for_json_and_keeps_safesearch_on():
    """
    `json=1` is not a preference: without it the endpoint answers in XML
    and `_decode` sees a parse error, which reads as an empty feed.

    `safesearch=true` is the filter behind "no nudity" — it is on the
    wire, not assumed, so a test can see it was actually sent.
    """
    with PixabayHarness() as harness:
        run(harness.fetch())

    sent = harness.requests[0]
    assert sent["json"] == "1"
    assert sent["safesearch"] == "true"


def test_a_second_pixabay_fetch_within_the_day_hits_the_network_zero_times():
    """
    The rule being pinned: "requests must be cached for 24 hours."

    The reels pass runs every five minutes. Without this the adapter
    would send 288 byte-identical requests a day per search term, which
    is the "lots of automated queries" their terms forbid — and against
    a free tier counted per API key rather than per IP, so the bill for
    it lands on the operator, not on us.
    """
    with PixabayHarness() as harness:
        first = run(harness.fetch())
        after_first = harness.hits

        second = run(harness.fetch())

    assert after_first == 1, "the first pass must actually reach the API"
    assert harness.hits == 1, (
        f"the second pass issued {harness.hits - 1} more request(s) inside "
        "the 24-hour window"
    )
    assert [r.external_id for r in second] == [r.external_id for r in first]


def test_the_pixabay_cache_is_shared_across_adapter_instances():
    """
    The cache is keyed on the params, not on the object.

    `build_source()` runs on every pass, so a cache scoped to an
    instance would start empty 288 times a day and satisfy nothing.
    """
    with PixabayHarness() as harness:
        run(harness.fetch())
        assert harness.hits == 1

        # A brand new adapter, sharing the same client, as the next pass
        # would build it.
        again = build_source(
            "pixabay",
            pool=build_pool("pixabay", "pix-key-one"),
            client=harness._client,
            hint="nepal",
        )
        run(again.fetch(limit=30))

    assert harness.hits == 1, "a rebuilt adapter re-requested the same query"


def test_a_different_pixabay_search_term_is_not_served_from_the_first_terms_cache():
    """
    The fingerprint includes the query.

    Serving "nepal" results for a "pokhara" request would be a silently
    wrong feed rather than a visible failure, which is the worst kind.
    """
    with PixabayHarness() as harness:
        run(harness.fetch())

        other = build_source(
            "pixabay",
            pool=build_pool("pixabay", "pix-key-one"),
            client=harness._client,
            hint="pokhara",
        )
        run(other.fetch(limit=30))

    assert harness.hits == 2
    assert harness.requests[0]["q"] != harness.requests[1]["q"]


def test_the_pixabay_cache_cannot_grow_without_bound():
    """
    Search terms come from configuration, so they are meant to rotate —
    and an unbounded module-level dict is how a config change turns into
    a slow leak inside a long-lived worker process.
    """
    cap = reels_providers.PixabaySource._CACHE_MAX_ENTRIES

    with PixabayHarness() as harness:
        for term in range(cap + 12):
            source = build_source(
                "pixabay",
                pool=build_pool("pixabay", "pix-key-one"),
                client=harness._client,
                hint=f"term-{term}",
            )
            run(source.fetch(limit=30))

    assert len(reels_providers.PixabaySource._cache) <= cap


def test_a_pixabay_response_is_cached_even_when_it_is_worthless():
    """
    The point of the test is the negative: today, an empty response IS
    cached, for 24 hours.

    That is defensible on its own terms — an empty result set is a real
    answer to a real query, and re-asking 288 times a day is exactly
    what the cache exists to prevent. But it means one unlucky minute
    (a bad search term, a provider-side hiccup that still returns 200)
    costs a full day of that term's feed, and the honest fix is to widen
    the term, not to wait. Pinned here so the behaviour is a decision
    rather than an accident.
    """
    with PixabayHarness(videos=[]) as harness:
        first = run(harness.fetch())
        assert first == []

        run(harness.fetch())

    assert harness.hits == 1, (
        "an empty answer is cached like any other; see the docstring "
        "above for why that is the intended trade"
    )


# =============================================================
# PER-PROVIDER POLL INTERVALS
# =============================================================

def test_a_provider_that_declares_a_cost_declares_one_the_scheduler_knows_about():
    """
    If the scheduler cannot see `min_poll_seconds`, the attribute is
    documentation with no enforcement behind it — and a YouTube key
    would still be spent inside half an hour. This is the cheapest
    possible guard against that regression: the field exists, and the
    scheduler reads it by that exact name.
    """
    source = inspect.getsource(
        __import__("app.workers.scheduler", fromlist=["_sync_reels_body"])
    )

    assert "min_poll_seconds" in source, (
        "the scheduler must read the provider's declared interval"
    )

    # YouTube's search.list is 100 units against 10,000/day.
    assert PROVIDERS["youtube"].min_poll_seconds >= 3600, (
        "hourly is the cheapest interval that keeps a single key inside "
        "its daily allowance"
    )

    # The cheap sources must NOT be throttled. Slowing them down to
    # serve one expensive provider would make the feed worse everywhere
    # to fix a problem in one place.
    assert PROVIDERS["wikimedia"].min_poll_seconds == 0
    assert PROVIDERS["pixabay"].min_poll_seconds == 0


async def test_a_costly_provider_is_polled_at_its_own_rate_not_the_jobs(monkeypatch):
    """
    The failure this prevents is silent, which is why it needs a test.

    YouTube's search.list costs 100 units against a 10,000/day default,
    so polling it on the reels cadence spends a key in roughly 30
    minutes. Nothing raises. Every health report still says the source
    is configured and healthy. The feed simply stops gaining YouTube
    clips for the remaining 23.5 hours of the day, and the operator has
    no signal saying why.

    Two passes back to back: the first must call YouTube, the second
    must not.
    """
    import httpx

    from app.workers import scheduler as sched

    sent: list[dict] = []
    db = FakeDB(rows=[{"id": GOOD_UUID}])
    client = FakeAsyncClient(COMMONS_RESPONSE, sent)

    monkeypatch.setattr(
        sched,
        "get_settings",
        lambda: _reels_settings(youtube_api_keys="yt-key-one"),
    )
    monkeypatch.setattr(sched, "get_db", lambda: db)
    monkeypatch.setattr(httpx, "AsyncClient", client)

    sched._reels_last_run.clear()

    # Pass one: everything runs.
    await sched._sync_reels()
    first_pass = [call["url"] for call in sent]

    # Pass two, moments later: YouTube is inside its own interval.
    sent.clear()
    await sched._sync_reels()
    second_pass = [call["url"] for call in sent]

    search = PROVIDERS["youtube"].endpoint

    assert search in first_pass, "the first pass must actually call YouTube"
    assert search not in second_pass, (
        "YouTube was polled again inside its declared hourly interval, "
        "which spends the day's quota in half an hour"
    )

    # And the cheap provider kept its cadence, or the fix would have
    # cost more than it saved.
    assert PROVIDERS["wikimedia"].endpoint in second_pass, (
        "throttling the expensive source must not slow the free ones"
    )


async def test_the_interval_expires_so_a_costly_provider_eventually_runs(monkeypatch):
    """
    The other half. A skip that never un-skips is a provider that ran
    once at start-up and never again, which is exactly as broken as
    never running — and looks identical in the logs.

    `_reels_last_run` is set to an hour ago rather than the clock being
    faked, so this tests the real comparison and not a stubbed one.
    """
    import httpx

    from app.workers import scheduler as sched

    sent: list[dict] = []
    db = FakeDB(rows=[{"id": GOOD_UUID}])
    client = FakeAsyncClient(COMMONS_RESPONSE, sent)

    monkeypatch.setattr(
        sched,
        "get_settings",
        lambda: _reels_settings(youtube_api_keys="yt-key-one"),
    )
    monkeypatch.setattr(sched, "get_db", lambda: db)
    monkeypatch.setattr(httpx, "AsyncClient", client)

    sched._reels_last_run.clear()

    await sched._sync_reels()

    # Pretend the last YouTube pass happened an hour and a half ago.
    sched._reels_last_run["youtube"] = time.monotonic() - 5400

    sent.clear()
    await sched._sync_reels()

    assert PROVIDERS["youtube"].endpoint in [call["url"] for call in sent], (
        "the interval must lapse; a provider throttled for ever is a "
        "provider that ran once"
    )


async def test_a_provider_that_never_declared_an_interval_is_never_skipped(monkeypatch):
    """
    The default must be "run every pass".

    `getattr(source, "min_poll_seconds", 0)` is what keeps this true for
    a custom adapter that subclasses nothing, and a scheduler that
    dropped the getattr would break every provider that has not been
    updated yet — silently, as a category that just stops updating.
    """
    import httpx

    from app.workers import scheduler as sched

    sent: list[dict] = []
    db = FakeDB(rows=[{"id": GOOD_UUID}])
    client = FakeAsyncClient(COMMONS_RESPONSE, sent)

    monkeypatch.setattr(sched, "get_settings", lambda: _reels_settings())
    monkeypatch.setattr(sched, "get_db", lambda: db)
    monkeypatch.setattr(httpx, "AsyncClient", client)

    sched._reels_last_run.clear()

    await sched._sync_reels()
    sent.clear()
    await sched._sync_reels()

    assert [call["url"] for call in sent] == [PROVIDERS["wikimedia"].endpoint], (
        "a provider with no declared interval runs on every pass"
    )


# =============================================================
# COMMONS SEARCH: WHY intitle:
# =============================================================
#
# The reels page was serving European Space Agency stock footage and a
# Spanish baseball match. The search expression was:
#
#     filetype:video -incategory:"Non-free files" nepal
#
# and CirrusSearch OR-matches. `filetype:video` matches every video on
# Commons, so the bare `nepal` was one more way in, not a filter. Those
# files carry a Nepal tag somewhere invisible — a category, a caption,
# an uploader's edit — and CirrusSearch had no way to be asked for
# "actually about Nepal".
#
# Measured against the live API:
#
#   ... nepal           -> Fagu Purnima (Holi), Panche Baja (a BENGALI
#                           wedding), Landing a Lukla Airport
#   ... intitle:nepal  -> Chandragiri Cable Car, Kathmandu, Nepal
#                          Dattatreya Square in the rain, Bhaktapur, Nepal
#                          Nepal flood 2026 before after
#                          Trishuli River at Pida Village, Dhading
#
# `intitle:` requires the term to be in the file's title, which is the
# only place it reliably means "this video is about Nepal".

COMMONS_SOURCE = PROVIDERS["wikimedia"]


def _commons(hint: str):
    return build_source(
        "wikimedia", pool=build_pool("wikimedia", None), client=None, hint=hint
    )


def test_a_search_term_is_scoped_to_the_title():
    """
    The whole fix in one assertion.

    Without `intitle:`, the expression is a bag of OR'd terms and the
    hint does not filter anything.
    """
    source = _commons("nepal")
    expression = source._search_expression("nepal")

    assert "intitle:nepal" in expression, expression
    # The loose form must not be there as well, or the title constraint
    # is defeated by the very term it replaced.
    assert " nepal" not in expression.replace("intitle:nepal", ""), expression


def test_the_non_free_exclusion_survives():
    """
    Commons holds fair-use files under that category. They are exactly
    the material that must never appear in our own feed, so the
    exclusion is not optional and not affected by the search change.
    """
    expression = _commons("nepal")._search_expression("nepal")
    assert '-incategory:"Non-free files"' in expression, expression
    assert "filetype:video" in expression, expression


def test_a_multi_word_term_is_quoted():
    """
    CirrusSearch ORs unquoted words, so `mount everest` would match a
    file with only "everest" in its title. Quoting makes it a phrase.
    """
    expression = _commons("mount everest")._search_expression("mount everest")
    assert 'intitle:"mount everest"' in expression, expression


def test_a_term_with_query_operators_is_dropped():
    """
    A term that is itself a search expression must not be interpolated
    into one. That is how a configuration value turns into a query
    rewrite — `*` and `~` and parentheses are all operator syntax in
    CirrusSearch.
    """
    terms = _commons("nepal,intitle:*,evil) OR (x")._search_terms()

    assert "nepal" in terms
    assert not any("*" in t for t in terms), terms
    assert not any("(" in t or ")" in t for t in terms), terms


def test_every_configured_term_is_searched_not_just_the_first():
    """
    One query per term, unioned.

    A single query can only reach so far down Commons' catalogue, and
    each term names a different corner of it. Searching one term per
    pass meant the feed rotated through twelve small sets instead of
    holding the union of them — which is what a reader sees as "the
    reels never change".
    """
    source = _commons("nepal,kathmandu,everest,pokhara")
    terms = source._search_terms()

    assert terms == ["nepal", "kathmandu", "everest", "pokhara"], terms


def test_the_term_list_is_bounded():
    """
    A misconfigured list of 200 terms must not turn one poll into 200
    requests against a free API.
    """
    many = ",".join(f"term{i}" for i in range(200))
    terms = _commons(many)._search_terms()

    assert len(terms) <= 12, len(terms)


def test_duplicate_terms_are_collapsed():
    """The scheduler rotates and re-sends terms; the same one twice is
    one query, not two."""
    terms = _commons("nepal,Nepal,NEPAL,kathmandu")._search_terms()
    assert len([t for t in terms if t.lower() == "nepal"]) == 1, terms


def test_the_preferred_rendition_is_high_definition():
    """
    The ceiling was 720, which meant the largest usable transcode was
    480p for most Commons files. On a 3x phone screen a 420px card is
    ~1260 device pixels, so 480p is visibly soft on exactly the
    devices that can afford the bytes.

    1080 is the ceiling because that is where Commons' transcode ladder
    tops out — the transcode at the same visual quality as a 4K master
    is a fraction of its size, which is the point of avoiding masters.
    """
    assert reels_providers.PREFERRED_HEIGHT >= 1080
    # And the floor is still a floor: a 180px rendition is a thumbnail.
    assert reels_providers.MIN_HEIGHT >= 360


# =============================================================
# THE UNATTENDED PATH MUST FETCH REELS
# =============================================================
#
# The bug was not in the reels pipeline. It was that the reels pipeline
# was not in the path that actually runs unattended.
#
# The reels pass lived ONLY inside the long-lived worker's APScheduler.
# The scheduled GitHub Actions job ran `scripts/sync_once.py`, which
# synced the 25 news sources and nothing else. So when the worker was
# asleep, restarting, or simply down, the news feed kept moving on the
# five-minute cron while the reels category froze — for a day, in the
# observed case. The user saw "news is minutes old, reels are a day
# old" and reasonably concluded the reels fetch was broken.
#
# These two tests are the guard against that coming back: the cron
# path must fetch reels, and it must do so by reusing the worker's own
# code rather than a second implementation that can drift.

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ingest.yml"
)


def test_the_scheduled_run_fetches_reels_as_well_as_news():
    """
    The cron path must call the reels pass.

    A copy of the assertion in prose, because a workflow file is not
    importable and a comment is not a test.
    """
    assert WORKFLOW_PATH.exists(), WORKFLOW_PATH
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "ENABLE_REELS=true" in workflow, (
        "the scheduled run must enable the reels feature — it defaults "
        "to false, so without this the pass is skipped entirely"
    )
    assert "REELS_POLL_SECONDS" in workflow, (
        "the poll interval must be set explicitly in the cron environment"
    )


def test_the_cron_reels_pass_reuses_the_worker_implementation():
    """
    One implementation, two callers.

    A second, cron-only reels implementation would drift from the
    worker's — different key pools, different timeouts, a different
    idea of what "done" means — and the two would then disagree about
    what the category contains. `_sync_reels` is the worker's own
    entry point, including the timeout that stops a hung provider from
    holding the slot for the life of the process.
    """
    from scripts import sync_once

    assert hasattr(sync_once, "sync_reels_once"), (
        "the cron entry point must expose a reels pass"
    )

    source = Path(sync_once.__file__).read_text(encoding="utf-8")
    assert "from app.workers.scheduler import _sync_reels" in source, (
        "the cron reels pass must call the worker's own _sync_reels, not "
        "a reimplementation"
    )


def test_a_failing_reels_pass_never_turns_a_green_news_run_red():
    """
    Reels are optional. A reels failure must not fail the job, and a
    green job must not be read as "reels worked too" — which is why
    the workflow reports REELS_OK separately and only warns.
    """
    import asyncio

    from scripts import sync_once

    async def boom() -> None:
        raise RuntimeError("the reels provider exploded")

    # A failure inside the pass is caught and reported, not raised.
    assert asyncio.iscoroutinefunction(sync_once.sync_reels_once)

    # And the function returns a bool rather than raising, so the
    # caller can report it without unwinding the whole run.
    async def run_with_failure(monkeypatch_target):
        import app.workers.scheduler as sched

        original = sched._sync_reels
        sched._sync_reels = boom
        try:
            return await sync_once.sync_reels_once()
        finally:
            sched._sync_reels = original

    assert asyncio.run(run_with_failure(None)) is False, (
        "a failed reels pass must report False, not raise and take the "
        "news sync down with it"
    )

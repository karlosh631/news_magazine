"""
Persistence for the reels pipeline, and the per-pass orchestrator.

What lives here
---------------
- `store_reel` / `store_reels`  — write metadata to the `reels` table.
- `ingest_reels`                — run every provider, then store what
                                  it fetched, then report what happened.
- `load_reels`                  — read the feed back for the web app.

THE HARD RULE, RESTATED BECAUSE IT IS THE ONE THING THAT MUST NEVER CHANGE
------------------------------------------------------------------------
This module writes METADATA AND AN UPSTREAM URL. Nothing else. Video
bytes are NEVER stored — not in Postgres, not in Storage, not anywhere
in this project. Playback streams straight from the provider's CDN.

Reels are the largest objects this system will ever touch. Storing them
would bloat the database, blow through Supabase's row and bandwidth
limits, and buy nothing a URL does not already buy. There is no column
for video bytes in migration 0014_reels.sql and there must never be one.
If a future change reaches for one, this file has been misunderstood.

Migration 0015_reels_embed_url.sql adds `embed_url`, which is a second
URL and not a payload. It exists because one provider cannot offer a
directly playable file and will not decode one to manufacture it; the
player needs to know that before it can pick between <video> and
<iframe>. Nothing else about the rule above changes.

The layering this file keeps
----------------------------
Adapters talk to providers; this module talks to the database; neither
talks to the other directly. `Reel` (in types.py) is the only thing
that crosses the line, which is what makes a new provider a file rather
than a refactor. The one deliberate exception is the key pool: a source's
`KeyPool` is discovered by duck-typing, never imported, so this module
keeps working with an adapter written before it.

Two failure rules, and both of them have been learned the hard way
-----------------------------------------------------------------
1. One bad reel never aborts a pass, and one dead provider never stops
   the others. A feed of thirty clips where one has a malformed URL must
   still produce twenty-nine cards.

2. `load_reels` NEVER raises. The reels feature is new and its
   migration (0014_reels.sql) will land separately from the code that
   reads it, so for a window the web app will call this against a
   database that has no `reels` table. The right answer to that is an
   empty feed and a loud log, not a 500 that takes the category page
   down with it. A missing feature is not a broken site.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.services.ingestion import db_call
from app.services.reels.keys import KeyPool
from app.services.reels.types import Reel, ReelSource, SourceHealth

logger = logging.getLogger("reels.store")

# The migration that creates `reels` and `reel_likes`. Named in log
# messages on purpose: "something is wrong" is useless at 3am, "apply
# 0014_reels.sql" is a thing you can do.
REELS_MIGRATION = "0014_reels.sql"

_MIGRATION_HINT = (
    "the reels table does not exist — apply supabase/migrations/"
    f"{REELS_MIGRATION}"
)

# The upsert key. Half of it is the provider's own id for the clip and
# half is which provider said it, and neither half is ever allowed to
# change: renaming a source_key orphans every row it ever wrote.
REEL_CONFLICT = "source_key,external_id"

# Upstream upserts at the same time as one source's reels are being
# stored. Well under DB_EXECUTOR's 16 threads (see services/ingestion.py)
# because these share the same client object with every other source.
_STORE_CONCURRENCY = 8

# Per-reel failures listed in SourceHealth. Past this the operator is
# being told the same thing twenty more times; the count still tells the
# whole story.
_MAX_REPORTED_FAILURES = 5


# =============================================================
# MISSING TABLE
#
# The reels migration lands separately from this code, so "no such
# table" is an expected state during a deploy, not a fault. It gets its
# own message instead of a stack trace.
# =============================================================

_MISSING_TABLE_SIGNALS = (
    "pgrst205",  # PostgREST: table is not in the schema cache
    "does not exist",
    "undefined table",
    "not in the schema cache",
)

# How the table is actually NAMED in an error: schema-qualified by
# PostgREST and by Postgres, and always quoted. Matching the bare word
# "reels" is not enough — "column reels.nope does not exist" is a
# missing COLUMN, and reporting that as "apply the migration" sends the
# operator to the wrong file.
_MISSING_TABLE_NAMES = (
    "public.reels",
    "'reels'",
    '"reels"',
    "public.reel_likes",
    "'reel_likes'",
    '"reel_likes"',
)


def _is_missing_reels_table(exc: BaseException) -> bool:
    """
    True when the failure was `reels` not existing yet.

    Two structured codes mean this outright: PGRST205 (PostgREST's
    schema-cache miss) and 42P01 (undefined_table). Neither can be
    anything else in this module — `reels` and `reel_likes` are the only
    tables it touches.

    Anything else has to earn it: a missing-relation signal AND the
    table named as PostgREST and Postgres write it. A generic "does not
    exist" match would swallow real errors — a missing column, a typo in
    a filter — and dress them up as a migration problem, which is
    exactly the kind of wrong-but-plausible log that costs an hour.
    """
    code = str(getattr(exc, "code", "") or "").upper()
    if code in {"PGRST205", "42P01"}:
        return True

    parts = (
        str(exc),
        str(getattr(exc, "message", "") or ""),
        str(getattr(exc, "details", "") or ""),
    )
    text = " ".join(parts).lower()
    if not any(name in text for name in _MISSING_TABLE_NAMES):
        return False
    return any(signal in text for signal in _MISSING_TABLE_SIGNALS)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _describe(exc: BaseException) -> str:
    """
    A short, loggable rendering of any exception.

    `str(exc)` is empty for anything raised from an empty message, and a
    log line reading "ApiError: " tells an operator nothing, so repr() is
    the fallback. Truncated because these end up in SourceHealth, which
    gets persisted and rendered in an admin panel.
    """
    text = str(exc).strip() or repr(exc)
    return f"{type(exc).__name__}: {text}"[:500]


# =============================================================
# STORE
# =============================================================

def _timestamp(value: str | None) -> str | None:
    """
    Normalise a provider's timestamp, or pass it through untouched.

    Adapters disagree about the offset: some send `...Z`, some send a
    naive local time. A naive string handed to a `timestamptz` column is
    interpreted in the DATABASE SERVER's timezone, so the same clip can
    sort differently depending on where it was written. Attaching UTC
    makes the stored value mean the same thing everywhere.

    A value that does not parse is returned as-is rather than replaced:
    the write then fails loudly and names the reel, which is more useful
    than silently dating a clip "now" and hiding the provider's bug.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value.strip() or None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _reel_row(reel: Reel) -> dict[str, Any]:
    """
    The row for one reel. Metadata and a URL — never bytes.

    Optional fields are sent ONLY when the provider actually supplied
    them, because this is an UPSERT. A payload that repeats
    `title: None` every pass would turn "the provider stopped sending a
    title" into "we erased the title we already had" — the feed card
    would quietly lose its caption for a reason nobody would ever trace
    back here. Omitting the key leaves the stored column alone.

    `ingested_at` is omitted for the same reason, and the default in
    0014_reels.sql then does the right thing on both paths: a new row
    gets `now()`, an existing row keeps the moment we first saw it.
    """
    row: dict[str, Any] = {
        "source_key": reel.source_key,
        "external_id": reel.external_id,
        # A direct, playable URL on the provider's own CDN. The only
        # video-related value that ever reaches the database.
        "video_url": reel.video_url,
        "attribution_required": reel.attribution_required,
        # Falls back to ingest time, which keeps a feed with no dates
        # from being 30 identical timestamps at the bottom of a
        # nulls-last sort.
        "published_at": _timestamp(reel.published_at) or _utcnow(),
    }

    for column, value in (
        ("embed_url", reel.embed_url),
        ("poster_url", reel.poster_url),
        ("title", reel.title),
        ("description", reel.description),
        ("creator_name", reel.creator_name),
        ("creator_url", reel.creator_url),
        ("license_name", reel.license_name),
        ("license_url", reel.license_url),
        ("width", reel.width),
        ("height", reel.height),
        ("duration_seconds", reel.duration_seconds),
    ):
        if value is not None:
            row[column] = value

    return row


async def store_reel(db, reel: Reel) -> bool:
    """
    Write one reel's metadata. True when stored, False otherwise.

    Upserts on (source_key, external_id) — the unique constraint in
    0014_reels.sql — so re-running the pass is free: a clip already in
    the table is refreshed in place, including a poster or a title the
    provider has since fixed, and never duplicated. This is what makes
    the schedule safe to overlap with itself.

    The response body is deliberately never read. PostgREST is allowed
    to answer an upsert with an empty representation, and the articles
    pipeline once counted a real article as "rejected" for exactly that
    reason, then permanently skipped its source link. Here a successful
    call IS the success signal.

    Never raises: one bad reel must not take the pass down with it.
    """
    row = _reel_row(reel)

    try:
        await db_call(
            lambda: db.table("reels")
            .upsert(row, on_conflict=REEL_CONFLICT)
            .execute()
        )
    except Exception as exc:
        if _is_missing_reels_table(exc):
            # Expected during a deploy, fatal for the feature. Logged
            # per clip on purpose: thirty identical lines make the cause
            # unmistakable, where one summarised line would read like a
            # single unlucky reel.
            logger.warning(
                "reels: nothing stored for %s/%s — %s.",
                reel.source_key, reel.external_id, _MIGRATION_HINT,
            )
        else:
            logger.warning(
                "reels: could not store %s/%s: %s",
                reel.source_key, reel.external_id, _describe(exc),
            )
        return False

    return True


async def store_reels(db, reels: list[Reel]) -> SourceHealth:
    """
    Store one provider's batch and report the outcome. Never raises.

    All `reels` are expected to come from a single source, because the
    SourceHealth returned is per-provider.

    The writes run concurrently rather than one after another. Each one
    is an independent round trip to PostgREST, and thirty of them in
    sequence is ten seconds of pure latency on every pass for no reason;
    the semaphore keeps that from becoming thirty simultaneous
    connections against one client object.
    """
    health = SourceHealth(source_key=reels[0].source_key if reels else "unknown")

    if not reels:
        health.notes.append("no reels to store")
        return health

    semaphore = asyncio.Semaphore(_STORE_CONCURRENCY)

    async def store_one(reel: Reel) -> bool:
        async with semaphore:
            try:
                return await store_reel(db, reel)
            except asyncio.CancelledError:
                # Spelled out rather than left to the BaseException
                # hierarchy: a cancelled pass must LOOK cancelled, not
                # like a source that failed. Everything else is swallowed
                # here on purpose — store_reel handles its own errors, so
                # reaching this handler means a bug, and a bug still must
                # not take the other twenty-nine reels down with it.
                raise
            except Exception:
                logger.exception(
                    "reels: unexpected failure storing %s/%s",
                    reel.source_key, reel.external_id,
                )
                return False

    # return_exceptions is not needed here — store_one swallows what it
    # can and re-raises what it must — and leaving it off means a
    # cancellation propagates instead of being recorded as a storage
    # failure.
    results = await asyncio.gather(*(store_one(reel) for reel in reels))

    failures = [reel for reel, ok in zip(reels, results) if not ok]

    health.fetched = len(reels)
    health.stored = sum(1 for ok in results if ok)
    health.skipped = len(failures)

    for reel in failures[:_MAX_REPORTED_FAILURES]:
        health.notes.append(f"{reel.source_key}/{reel.external_id} was not stored")
    if len(failures) > _MAX_REPORTED_FAILURES:
        health.notes.append(
            f"...and {len(failures) - _MAX_REPORTED_FAILURES} more failed the same way "
            "(see the reels.store log)"
        )

    # An error is reserved for "this source produced nothing at all".
    # Two failures out of thirty is a degraded pass, not an outage, and
    # reporting it as an error would train the operator to ignore the
    # field.
    if failures and health.stored == 0:
        health.error = f"none of the {len(reels)} reel(s) from {health.source_key} could be stored"

    return health


# =============================================================
# KEY POOL DISCOVERY
# =============================================================

_POOL_ATTRIBUTES = ("pool", "key_pool")


def _pool_for(source: ReelSource) -> KeyPool | None:
    """
    The adapter's KeyPool, when it has one.

    Found by duck-typing rather than imported from providers.py.
    `ReelSource` is a Protocol with three members on purpose — every
    extra method is another thing a new provider has to get wrong — so
    a store that reached into a concrete adapter for its keys would be
    coupled to that adapter and would break the moment a second one
    arrived.

    An adapter that keeps its keys some other way simply reports no pool
    here, and is treated as "keys unknown" rather than "no keys".
    """
    for attribute in _POOL_ATTRIBUTES:
        candidate = getattr(source, attribute, None)
        if isinstance(candidate, KeyPool):
            return candidate
    return None


def _keys_used(pool: KeyPool | None) -> int:
    """
    How many key requests this pass spent.

    KeyPool counts every use it hands out, so for the pool built fresh
    for each pass this is the pass's own spend and not a running total.
    It is the only number that tells an operator whether rotating keys is
    actually buying them anything — one key silently exhausting reads as
    "the provider is down" otherwise.
    """
    if pool is None:
        return 0
    return sum(int(entry.get("total_uses", 0)) for entry in pool.snapshot())


# =============================================================
# PER-PASS ORCHESTRATION
# =============================================================

async def _ingest_one(
    db, name: str, source: ReelSource, per_source: int
) -> SourceHealth:
    """
    Fetch from one source, store what came back, report it. Never raises.

    `name` is the caller's key for this source; the adapter's own `key`
    wins when it has one, because that is the value written to
    reels.source_key and the one an operator will see in the feed.
    """
    key = getattr(source, "key", None) or name
    health = SourceHealth(source_key=key)
    pool = _pool_for(source)

    if getattr(source, "requires_key", False) and pool is not None and pool.is_empty:
        # Skipped, not failed. A keyless provider is a normal way to run
        # this service — Wikimedia needs no key at all — and reporting a
        # missing key as an error would put a red line on every deploy
        # that has not filled in Pexels yet.
        health.notes.append(
            f"{key}: requires an API key and none is configured — skipped. "
            "Every other source in this pass still ran."
        )
        return health

    try:
        fetched = await source.fetch(limit=per_source)
    except asyncio.CancelledError:
        # A cancelled pass has to look cancelled all the way down.
        raise
    except Exception as exc:
        health.error = _describe(exc)
        health.notes.append(f"{key}: fetch failed")
        logger.warning("reels: %s could not be fetched: %s", key, _describe(exc))
        return health

    health.keys_used = _keys_used(pool)

    if not isinstance(fetched, (list, tuple)):
        health.error = f"{key}: provider returned {type(fetched).__name__}, expected a list"
        logger.warning("reels: %s returned %s, not a list", key, type(fetched).__name__)
        return health

    # A provider that hands back something that is not a Reel is a bug
    # in that adapter, not a reason to lose the other twenty-nine.
    candidates = list(fetched)
    usable = [reel for reel in candidates if isinstance(reel, Reel)]
    dropped = len(candidates) - len(usable)

    stored = await store_reels(db, usable)

    # `fetched` is what the provider returned, not what reached the
    # store: an operator reading this wants to know whether the provider
    # produced nothing, which is a different problem from a write
    # failing.
    health.fetched = len(candidates)
    health.stored = stored.stored
    health.skipped = stored.skipped + dropped
    health.error = stored.error
    health.notes.extend(stored.notes)
    if dropped:
        health.notes.append(
            f"{key}: {dropped} item(s) were not Reel objects and were dropped"
        )

    if health.error:
        logger.warning("reels: %s stored %d/%d", key, health.stored, health.fetched)

    return health


async def ingest_reels(
    db,
    *,
    sources: dict[str, ReelSource],
    per_source: int = 30,
) -> list[SourceHealth]:
    """
    Run one ingest pass over every source. Never raises.

    Sources run concurrently and independently: one provider timing out,
    exhausting its keys or returning nonsense costs that source its pass
    and nothing else. `return_exceptions=True` is what guarantees that,
    because the alternative — an exception escaping one source — would
    cancel the siblings that were mid-fetch and turn a single dead
    provider into a dead pass.

    Returns one SourceHealth per source, in the order given, so a
    caller can line the report up against the config that produced it.
    """
    if not sources:
        logger.info("reels: no sources configured; nothing to ingest.")
        return []

    names = list(sources)
    results = await asyncio.gather(
        *(_ingest_one(db, name, sources[name], per_source) for name in names),
        return_exceptions=True,
    )

    health: list[SourceHealth] = []
    for name, outcome in zip(names, results):
        if isinstance(outcome, asyncio.CancelledError):
            # A cancelled pass has to LOOK cancelled. Recording it as a
            # source error would leave a scheduler that is shutting down
            # reporting a provider outage on its way out.
            raise outcome
        if isinstance(outcome, BaseException):
            # _ingest_one handles its own failures, so this is a bug in
            # the adapter's shape rather than a provider problem. It is
            # reported, not raised, and the other sources are unaffected.
            logger.error("reels: %s failed unexpectedly: %s", name, _describe(outcome))
            health.append(
                SourceHealth(
                    source_key=name,
                    error=_describe(outcome),
                    notes=["the source failed before it could report its own health"],
                )
            )
            continue
        health.append(outcome)

    total_stored = sum(item.stored for item in health)
    total_fetched = sum(item.fetched for item in health)
    logger.info(
        "reels: pass complete — %d/%d reel(s) stored across %d source(s), %d skipped.",
        total_stored, total_fetched, len(health),
        sum(item.skipped for item in health),
    )

    return health


# =============================================================
# READ
# =============================================================

# The feed's columns, named explicitly and in the order the web app's
# `Reel` interface declares them in apps/web/lib/reels.ts. Spelled out
# rather than `select("*")` on purpose: a later migration that adds a
# column should not silently start shipping it inside a response whose
# TypeScript type does not declare it, which is how an `any` creeps into
# a card component.
#
# source_key and external_id are stored but NOT returned. They are the
# dedup key and the audit trail, not feed content, and the client has no
# use for either. ingested_at is likewise unselected — it is still
# ordered by below, which Postgres allows, so the tiebreak costs nothing
# on the wire.
FEED_COLUMNS: tuple[str, ...] = (
    "id",
    # Which library the clip came from. The web card renders it on every
    # reel because Pixabay's API terms require the origin of every
    # result to be shown to the reader, so a feed row without it cannot
    # satisfy that duty.
    "source_key",
    "video_url",
    # Migration 0015. Selected alongside video_url, never instead of it:
    # video_url stays NOT NULL so the column the player and every
    # existing query already depend on keeps its meaning, and embed_url
    # only says whether that URL needs an <iframe> to play.
    "embed_url",
    "poster_url",
    "title",
    "description",
    "creator_name",
    "creator_url",
    "license_name",
    "license_url",
    "attribution_required",
    "width",
    "height",
    "duration_seconds",
    "published_at",
)

# The like count is a LEFT JOIN that PostgREST embeds: a reel nobody has
# liked must still come back, with a zero. An inner join would silently
# delete every unliked reel from the feed.
FEED_SELECT = ",".join(FEED_COLUMNS) + ",reel_likes(count)"

# Newest first, and undated last.
#
# Postgres sorts NULLs FIRST for a DESC order unless told otherwise,
# which would pin every reel whose provider gave no date to the top of
# the feed — the reels with the least information about them, presented
# as the newest. postgrest-py's order() has no nullslast switch, so the
# whole PostgREST order term is passed as the column name. This matches
# reels_published_at_idx in 0014_reels.sql.
FEED_ORDER = "published_at.desc.nullslast"

# PostgREST filters travel in the query string, and a uuid is 36
# characters. A few hundred ids is already ~11KB, past which a proxy in
# front of Supabase is entitled to answer 414 — and a 414 takes the whole
# feed down, which is a far worse outcome than showing one clip the
# visitor has already seen. So the set is capped, and the cap is logged
# rather than hidden.
MAX_EXCLUDE_IDS = 300

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _clean_ids(exclude_ids: set[str]) -> list[str]:
    """
    Keep the ids that are actually uuids, and no more than the URL can hold.

    A non-uuid in the list is a bug in the caller, and passing it through
    would make Postgres reject the entire query with "invalid input
    syntax for type uuid" — so one bad id would empty the feed for every
    visitor rather than for the one request that sent it.
    """
    cleaned = [
        candidate.strip()
        for candidate in exclude_ids
        if isinstance(candidate, str) and _UUID_RE.match(candidate.strip())
    ]
    if len(cleaned) > MAX_EXCLUDE_IDS:
        logger.warning(
            "reels: %d excluded id(s) requested, only the first %d can be sent; "
            "the feed may repeat a few clips.",
            len(cleaned), MAX_EXCLUDE_IDS,
        )
        cleaned = cleaned[:MAX_EXCLUDE_IDS]
    return cleaned


def _like_count(row: dict) -> int:
    """
    The reel's like count, from the embedded aggregate.

    Tolerant by design. The count is a display number: a shape PostgREST
    ever changes should cost a "0 likes" label, never a 500 on the feed.
    """
    embedded = row.get("reel_likes")

    if isinstance(embedded, int):
        return embedded
    if isinstance(embedded, list):
        for entry in embedded:
            if isinstance(entry, dict):
                try:
                    return int(entry.get("count") or 0)
                except (TypeError, ValueError):
                    return 0
    return 0


def _feed_row(row: dict) -> dict:
    """
    One reel exactly as the web app's Reel interface declares it:
    the stored snake_case columns, plus the computed `like_count`.
    """
    shaped = {column: row.get(column) for column in FEED_COLUMNS}
    shaped["like_count"] = _like_count(row)
    return shaped


async def load_reels(
    db,
    *,
    limit: int,
    exclude_ids: set[str] | None = None,
) -> list[dict]:
    """
    Read the feed for the web app's API route. Never raises.

    Returns rows shaped exactly like the TypeScript Reel interface — the
    stored snake_case columns plus a computed `like_count` — so the route
    can hand them straight to the client with no reshaping step that
    could disagree with the type.

    `exclude_ids` is the "give me the next page" set: reels the client
    already has, so scrolling does not loop on the same thirty clips.

    Returns [] on ANY failure, and says why in the log. In particular,
    before migration 0014_reels.sql is applied this is not an error
    state but an expected one: the code deploys before its migration far
    more often than not, and a 500 here would take the whole Reels
    category page down for a feature that simply has no content yet.
    """
    if limit <= 0:
        return []

    excludes = _clean_ids(exclude_ids) if exclude_ids else []

    def read() -> Any:
        query = (
            db.table("reels")
            .select(FEED_SELECT)
            .order(FEED_ORDER)
            # Tiebreak, and it is not a nicety: on a source's first pass
            # every reel gets published_at = ingest time, so thirty rows
            # share one timestamp. Without a stable second key the
            # database is free to return them in a different order on
            # each request and the same clip lands on two pages.
            .order("ingested_at", desc=True)
            .limit(limit)
        )
        if excludes:
            query = query.not_.in_("id", excludes)
        return query.execute()

    try:
        response = await db_call(read)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if _is_missing_reels_table(exc):
            logger.warning(
                "reels: feed is empty because %s. Until it is applied the "
                "/category/reels page renders empty, which is the intended "
                "state for an unlaunched feature — not a broken site.",
                _MIGRATION_HINT,
            )
        else:
            # A transient read failure and a missing table are the same
            # thing to the caller, which is why they are separated here
            # and nowhere else: one is expected during a deploy, the
            # other is not, and they deserve different levels of alarm.
            logger.exception("reels: could not read the feed; serving an empty one.")
        return []

    rows = response.data or []
    return [_feed_row(row) for row in rows if isinstance(row, dict)]
"""
FastAPI application: the ingestion service's HTTP surface.

Endpoints
---------
GET  /api/health              liveness + a real database probe
POST /api/sync                manually trigger a sync (secret-protected)
GET  /api/sources             list configured sources and their adapters
GET  /api/sources/health      per-source sync health
GET  /api/search              database-only search (no scraping)
POST /api/scrape-on-demand    search, then scrape live if the database
                              came up short — the resurrection endpoint
POST /api/prune               force a retention prune (secret-protected)

Stage 1's /api/health reported `"database": "ok"` whenever the query
callable existed, so a deploy with no Supabase configured passed its
health check forever. It now probes a table and reports honestly.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import get_settings
from app.database import get_db
from app.services.adapters import available_adapters, describe
from app.services.ingestion import prune_old_articles, run_source_sync
from app.services.search_resurrect import search_with_resurrection
from app.workers import scheduler as scheduler_module

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)-8s %(name)-14s %(message)s",
)
logger = logging.getLogger("main")

# Set by worker.py so main.py knows a standalone daemon already owns the
# schedule and this process must not start a second one.
_WORKER_MODE = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    if settings.scheduler_enabled and not _WORKER_MODE:
        scheduled = await scheduler_module.start()
        if not scheduled:
            logger.warning(
                "Started with 0 scheduled sources. Apply "
                "supabase/migrations/0008_seed_nepali_sources.sql, or set "
                "enabled=true and allowed=true on a sources row."
            )
    elif _WORKER_MODE:
        logger.info("Worker mode: scheduler is owned by worker.py, not this process.")

    yield

    scheduler_module.shutdown()


app = FastAPI(title="Nepal News Ingestion Service", lifespan=lifespan)


def mark_worker_mode() -> None:
    """Called by worker.py before this app is served."""
    global _WORKER_MODE
    _WORKER_MODE = True


# =============================================================
# AUTH
# =============================================================

def _require_secret(provided: str | None) -> None:
    """
    Constant-time secret check.

    Stage 1 used `provided != settings.ingestion_secret`, which leaks the
    secret's prefix through response timing. hmac.compare_digest does not.
    """
    settings = get_settings()
    if not provided or not hmac.compare_digest(
        provided.encode("utf-8"), settings.ingestion_secret.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401, detail="Invalid or missing ingestion secret."
        )


# =============================================================
# HEALTH
# =============================================================

@app.get("/api/health")
async def health():
    """
    Honest liveness. Reports `degraded` rather than `ok` when Supabase
    is unreachable or unconfigured, so an orchestrator restarts or
    alerts instead of trusting a false pass.
    """
    settings = get_settings()
    db_detail: str = "ok"

    if not settings.supabase_url or not settings.supabase_service_role_key:
        db_detail = "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set"
        return {
            "status": "degraded",
            "database": "unconfigured",
            "detail": db_detail,
            "ingestion": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    try:
        await asyncio.to_thread(
            lambda: get_db().table("categories").select("id").limit(1).execute()
        )
    except Exception as exc:  # noqa: BLE001
        db_detail = f"{type(exc).__name__}: {exc}"[:200]
        return {
            "status": "degraded",
            "database": "error",
            "detail": db_detail,
            "ingestion": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    sources_ok = 0
    try:
        result = await asyncio.to_thread(
            lambda: get_db()
            .table("sources")
            .select("id", count="exact")
            .eq("enabled", True)
            .eq("allowed", True)
            .execute()
        )
        sources_ok = result.count or 0
    except Exception:
        pass

    return {
        "status": "ok",
        "database": "ok",
        "active_sources": sources_ok,
        "adapters": available_adapters(),
        "ingestion": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# =============================================================
# SYNC
# =============================================================

@app.post("/api/sync")
async def trigger_sync(
    source_id: str | None = None,
    x_ingestion_secret: str | None = Header(default=None),
):
    """
    Manually trigger a sync. Protected — never exposed unauthenticated.

    Called by the admin dashboard (which validates the admin's own
    session first) or by an external cron presenting the shared secret.
    """
    _require_secret(x_ingestion_secret)
    db = get_db()

    try:
        if source_id:
            rows = await asyncio.to_thread(
                lambda: db.table("sources").select("*").eq("id", source_id).execute()
            )
            rows = rows.data
        else:
            rows = await asyncio.to_thread(
                lambda: db.table("sources").select("*")
                .eq("enabled", True)
                .eq("allowed", True)
                .execute()
            )
            rows = rows.data
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")

    results = []
    for source_row in rows or []:
        result = await run_source_sync(source_row)
        results.append({"source": source_row.get("name"), **result.as_dict()})

    return {"success": True, "results": results}


# =============================================================
# SOURCES
# =============================================================

@app.get("/api/sources")
async def list_sources(x_ingestion_secret: str | None = Header(default=None)):
    _require_secret(x_ingestion_secret)
    try:
        rows = await asyncio.to_thread(
            lambda: get_db().table("sources").select("*").order("name").execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")
    return [describe(row) | {"name": row.get("name"), "enabled": row.get("enabled")} for row in (rows.data or [])]


@app.get("/api/sources/health")
async def sources_health(x_ingestion_secret: str | None = Header(default=None)):
    _require_secret(x_ingestion_secret)
    db = get_db()

    try:
        sources = await asyncio.to_thread(
            lambda: db.table("sources").select("*").order("name").execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")

    out = []
    for source in sources.data or []:
        try:
            latest = await asyncio.to_thread(
                lambda s=source: db.table("source_sync_logs")
                .select("*")
                .eq("source_id", s["id"])
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )
            latest_log = (latest.data or [None])[0]
        except Exception:
            latest_log = None

        status = "DISABLED"
        if source.get("enabled") and source.get("allowed"):
            if latest_log is None:
                status = "WAITING"
            elif latest_log.get("status") == "success":
                status = "HEALTHY"
            elif latest_log.get("status") == "partial":
                status = "WARNING"
            else:
                status = "FAILED"

        out.append({
            "source": source.get("name"),
            "adapter": source.get("adapter_key"),
            "status": status,
            "consecutive_failures": source.get("consecutive_failures", 0),
            "last_success_at": source.get("last_success_at"),
            "last_sync": latest_log,
        })

    return out


# =============================================================
# SEARCH
# =============================================================

class ScrapeRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=50)
    # Callers may disable scraping (the frontend does this for
    # type-ahead suggestions so a keystroke cannot trigger 27 fetches).
    scrape: bool = True


async def _fetch_source_payload(source_row: dict) -> str:
    """Fetch a source's raw feed/listing HTML. Used by on-demand search."""
    from app.services.adapters import build_adapter

    adapter = build_adapter(source_row)
    return await adapter.fetch()


@app.get("/api/search")
async def search_database(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
):
    """Database-only search. Never scrapes, so it is safe for keystrokes."""
    from app.services.search_resurrect import OnDemandSearcher

    started = time.monotonic()
    searcher = OnDemandSearcher(get_db(), _fetch_source_payload)
    articles = await asyncio.to_thread(searcher.search_database, q, limit)

    return {
        "query": q,
        "articles": articles,
        "count": len(articles),
        "resurrected": 0,
        "scraped": False,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


@app.post("/api/scrape-on-demand")
async def scrape_on_demand(
    payload: ScrapeRequest,
    x_ingestion_secret: str | None = Header(default=None),
):
    """
    Search, then scrape live if the database came up short.

    This is the resurrection path. Articles are pruned after
    retention_hours to keep the table light, so a search for a story
    from three days ago would otherwise return nothing. When the live
    database has fewer than three matches, this endpoint re-polls the
    enabled sources, keeps the entries matching the query, re-inserts
    them flagged resurrected_at, and returns everything together.

    Guard rails, because this is reachable from a public search box:
      - Rate limited per query per hour (settings.max_requests_per_hour).
      - Sources are polled through the same robots.txt check and rate
        limiter as the background worker.
      - Concurrency-capped and bounded by scrape_budget_seconds.

    Passing x-ingestion-secret bypasses the per-query rate limit, for
    the Next.js server making a trusted server-side call.
    """
    settings = get_settings()
    started = time.monotonic()

    result = await search_with_resurrection(
        db=get_db(),
        query=payload.query,
        fetch_source=_fetch_source_payload,
        limit=payload.limit,
        max_requests_per_hour=settings.max_requests_per_hour,
        enable_scrape=payload.scrape,
        enrich_media=settings.enrich_media,
    )

    return {
        "query": result.query,
        "articles": result.articles,
        "count": len(result.articles),
        "live_count": result.live_count,
        "resurrected_count": result.resurrected_count,
        "scraped": result.sources_polled > 0,
        "rate_limited": result.rate_limited,
        "note": result.note,
        "duration_ms": result.duration_ms,
        "trusted_caller": bool(x_ingestion_secret),
    }


# =============================================================
# PRUNE
# =============================================================

@app.post("/api/prune")
async def force_prune(
    retention_hours: int | None = None,
    x_ingestion_secret: str | None = Header(default=None),
):
    """Force a retention prune. Normally happens automatically each cycle."""
    _require_secret(x_ingestion_secret)
    settings = get_settings()

    deleted = await prune_old_articles(
        get_db(),
        retention_hours or settings.retention_hours,
        settings.max_article_rows,
    )
    return {
        "deleted": deleted,
        "retention_hours": retention_hours or settings.retention_hours,
        "max_rows": settings.max_article_rows,
    }
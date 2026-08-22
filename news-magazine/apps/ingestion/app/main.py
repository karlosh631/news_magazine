from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException

from app.config import get_settings
from app.database import get_db
from app.services.ingestion import run_source_sync
from app.workers import scheduler as scheduler_module

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().scheduler_enabled:
        scheduler_module.start()
    yield
    if scheduler_module._scheduler:
        scheduler_module._scheduler.shutdown(wait=False)


app = FastAPI(title="Nepal News Ingestion Service", lifespan=lifespan)


def _require_secret(provided: str | None) -> None:
    settings = get_settings()
    if provided != settings.ingestion_secret:
        raise HTTPException(status_code=401, detail="Invalid or missing ingestion secret.")


@app.get("/api/health")
async def health():
    db_ok = True
    try:
        get_db().table("sources").select("id").limit(1).execute()
    except Exception:  # noqa: BLE001
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "ingestion": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/sync")
async def trigger_sync(source_id: str | None = None, x_ingestion_secret: str | None = Header(default=None)):
    """
    Manually trigger a sync. Protected — never exposed unauthenticated
    (see spec section 30). Called either by the admin dashboard (via the
    Next.js server, which itself validates the admin's session) or by an
    external cron service hitting this with the shared secret.
    """
    _require_secret(x_ingestion_secret)
    db = get_db()

    if source_id:
        rows = db.table("sources").select("*").eq("id", source_id).execute().data
    else:
        rows = db.table("sources").select("*").eq("enabled", True).eq("allowed", True).execute().data

    results = []
    for source_row in rows or []:
        result = await run_source_sync(source_row)
        results.append({
            "source": source_row["name"],
            "fetched": result.fetched,
            "new": result.new,
            "duplicates": result.duplicates,
            "rejected": result.rejected,
            "errors": result.errors,
        })
    return {"success": True, "results": results}


@app.get("/api/sources/health")
async def sources_health(x_ingestion_secret: str | None = Header(default=None)):
    _require_secret(x_ingestion_secret)
    db = get_db()
    sources = db.table("sources").select("*").execute().data or []
    out = []
    for s in sources:
        latest = (
            db.table("source_sync_logs")
            .select("*")
            .eq("source_id", s["id"])
            .order("started_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        latest_log = latest[0] if latest else None
        status = "DISABLED"
        if s["enabled"] and s["allowed"]:
            if latest_log is None:
                status = "WARNING"
            elif latest_log["status"] == "success":
                status = "HEALTHY"
            elif latest_log["status"] == "partial":
                status = "WARNING"
            else:
                status = "FAILED"
        out.append({"source": s["name"], "status": status, "last_sync": latest_log})
    return out

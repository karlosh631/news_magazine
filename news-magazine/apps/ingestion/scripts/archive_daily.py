"""
Archive the day's ingested news and commit it to git.

Runs once a day (see scripts/install_daily_archive.py). Writes a
snapshot of everything the pipeline published that day to
data/daily/YYYY-MM-DD.json and commits it.

Why this exists: it gives you a durable, dated record of what the
pipeline actually published — useful for auditing coverage, spotting an
outlet that stopped producing, and reviewing dedup decisions after the
48-hour TTL has pruned the live rows.

Connectivity note: this needs the Supabase credentials to read the
articles table. Reads only — it never writes to the database.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

# scripts/archive_daily.py -> apps/ingestion/scripts/ -> apps/ingestion/
REPO = Path(__file__).resolve().parent.parent.parent
INGESTION_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the ingestion root before importing app.config.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(INGESTION_ROOT / ".env")
sys.path.insert(0, str(INGESTION_ROOT))

from app.config import get_settings  # noqa: E402
from app.database import get_db  # noqa: E402

OUT_DIR = REPO / "data" / "daily"
KATHMANDU = timezone(timedelta(hours=5, minutes=45))


def run_git(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def main() -> int:
    settings = get_settings()
    db = get_db()

    now = datetime.now(KATHMANDU)
    day = now.strftime("%Y-%m-%d")
    since = (now - timedelta(hours=24)).astimezone(timezone.utc).isoformat()

    # ---------------------------------------------------------------
    # Read what the pipeline actually published in the last 24h.
    # ---------------------------------------------------------------
    try:
        response = db.rpc(
            "latest_articles", {"p_limit": 500, "p_category": None, "p_exclude_ids": None}
        ).execute()
        rows = response.data or []
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read articles: {exc}")
        return 2

    recent = []
    for row in rows:
        published = row.get("published_at")
        if not published:
            continue
        try:
            when = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= now.astimezone(timezone.utc) - timedelta(hours=24):
            recent.append(row)

    sources: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    with_image = 0
    with_points = 0
    with_video = 0
    with_audio = 0

    articles = []
    for row in sorted(recent, key=lambda r: r.get("published_at") or "", reverse=True):
        if row.get("source_name_snapshot"):
            sources[row["source_name_snapshot"]] += 1
        if row.get("category_slug"):
            categories[row["category_slug"]] += 1
        languages["ne" if row.get("language") == "ne" else "en"] += 1
        if row.get("image_highres_url") or row.get("featured_image_url"):
            with_image += 1
        if row.get("summary_points"):
            with_points += 1
        if row.get("video_url") or row.get("video_embed_url"):
            with_video += 1
        if row.get("audio_url"):
            with_audio += 1

        articles.append(
            {
                "slug": row.get("slug"),
                "headline": row.get("headline"),
                "published_at": row.get("published_at"),
                "source": row.get("source_name_snapshot"),
                "source_url": row.get("source_article_url"),
                "category": row.get("category_slug"),
                "language": "ne" if row.get("language") == "ne" else "en",
                "reported_by_n_outlets": row.get("source_count", 1),
                "image": row.get("image_highres_url") or row.get("featured_image_url"),
                "summary_points": row.get("summary_points") or [],
                "has_video": bool(row.get("video_url") or row.get("video_embed_url")),
                "has_audio": bool(row.get("audio_url")),
            }
        )

    snapshot = {
        "date": day,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "counts": {
            "articles": len(articles),
            "by_source": dict(sources.most_common()),
            "by_category": dict(categories.most_common()),
            "by_language": dict(languages),
            "with_image": with_image,
            "with_summary_points": with_points,
            "with_video": with_video,
            "with_audio": with_audio,
        },
        "retention_hours": settings.retention_hours,
        "articles": articles,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"{day}.json"
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Wrote {target.relative_to(REPO)} — {len(articles)} article(s)")
    print(f"  by source   : {dict(sources.most_common(6))}")
    print(f"  by category : {dict(categories.most_common(6))}")
    print(f"  with image  : {with_image}/{len(articles)}")
    print(f"  with points : {with_points}/{len(articles)}")

    # ---------------------------------------------------------------
    # Commit
    # ---------------------------------------------------------------
    code, out = run_git(["add", str(target.relative_to(REPO)).replace("\\", "/")])
    if code != 0:
        print(f"git add failed: {out}")
        return 3

    code, out = run_git(["diff", "--cached", "--quiet"])
    if code == 0:
        print("Nothing to commit — today's snapshot is unchanged.")
        return 0

    message = (
        f"news archive {day}: {len(articles)} article(s) from "
        f"{len(sources)} outlet(s)"
    )
    code, out = run_git(["commit", "-m", message])
    if code != 0:
        print(f"git commit failed: {out}")
        return 3

    print(f"Committed: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
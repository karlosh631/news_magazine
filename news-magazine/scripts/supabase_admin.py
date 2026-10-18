"""
Supabase Management API SQL runner.

Uses the sbp_ personal access token from apps/web/.env.local to run
migration SQL against the linked project. Credentials are never printed
and never leave this machine other than as an Authorization header.

Modes:
    status   read-only inspection of the current schema
    plan     list migrations and what each changes, applying none
    apply    apply pending migrations one at a time, stopping on error

The SQL sent is exactly the contents of supabase/migrations/*.sql in
this repository. Nothing is generated or modified.

Prerequisite: migrations/0001..0006 must already be applied (they are
Stage 1's own schema). This runner reports which files applied cleanly.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / "apps" / "web" / ".env.local"
MIGRATIONS = REPO / "supabase" / "migrations"
ENDPOINT = "https://api.supabase.com/v1/projects/{ref}/database/query"


def load_env(path: Path) -> dict:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def project_ref(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return host.split(".")[0]


def run_sql(token: str, ref: str, sql: str, timeout: int = 300):
    """POST one SQL batch to the Management API."""
    request = urllib.request.Request(
        ENDPOINT.format(ref=ref),
        data=json.dumps({"query": sql}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body.strip() else []
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from None


# ---------------------------------------------------------------------
# STATUS
# ---------------------------------------------------------------------

def cmd_status(token: str, ref: str) -> int:
    print(f"Project ref: {ref}\n")

    tables = run_sql(
        token,
        ref,
        """
        select table_name from information_schema.tables
        where table_schema = 'public' order by table_name;
        """,
    )
    names = [row["table_name"] for row in tables]
    print(f"Public tables ({len(names)}): {', '.join(names)}\n")

    columns = run_sql(
        token,
        ref,
        """
        select column_name from information_schema.columns
        where table_schema='public' and table_name='articles'
        order by column_name;
        """,
    )
    column_names = [row["column_name"] for row in columns]
    print(f"articles columns ({len(column_names)}):")
    print("  " + ", ".join(column_names))

    for expected in ("summary_points", "title_normalized", "image_highres_url", "duplicate_of"):
        if expected in column_names:
            print(f"  [present] {expected}")
        else:
            print(f"  [MISSING] {expected}  (needs migration 0007)")

    for table, label in (("sources", "sources"), ("articles", "articles"), ("categories", "categories")):
        try:
            result = run_sql(token, ref, f"select count(*)::int as n from {table};")
            print(f"\n{table}: {result[0]['n']} row(s)")
        except Exception as exc:  # noqa: BLE001
            print(f"\n{table}: query failed - {str(exc)[:120]}")

    functions = run_sql(
        token,
        ref,
        """
        select routine_name from information_schema.routines
        where routine_schema='public'
          and routine_name in ('search_articles','latest_articles',
            'prune_expired_articles','find_duplicate_candidates',
            'increment_article_source_count')
        order by routine_name;
        """,
    )
    found = [row["routine_name"] for row in functions]
    print(f"\nRPC functions present ({len(found)}/5): {', '.join(found) or 'none'}")

    ledger = run_sql(
        token,
        ref,
        "select exists (select 1 from information_schema.tables "
        "where table_schema='public' and table_name='_applied_migrations') as has_ledger;",
    )
    print(f"Migration ledger present: {ledger[0]['has_ledger']}")
    return 0


# ---------------------------------------------------------------------
# PLAN
# ---------------------------------------------------------------------

NOTES = {
    "0001_init_schema.sql": "core schema (articles, sources, categories, users, ads, ...)",
    "0002_rls_policies.sql": "RLS enabled on every table + policies",
    "0003_functions_triggers.sql": "updated_at + search_vector triggers, trending view",
    "0004_article_media_columns.sql": "adds video_url / audio_url / gif_url",
    "0005_seed_public_categories.sql": "inserts public category taxonomy",
    "0006_remove_demo_content.sql": "deletes Stage 1 demo fixtures (3 rows)",
    "0007_ingestion_hardening.sql": "dedup lineage + TTL columns + summary_points + 4 SQL "
        "functions. Grants EXECUTE on latest_articles()/search_articles() to "
        "anon+authenticated+service_role (read-only, published-rows-only) and to "
        "service_role alone for find_duplicate_candidates()/prune_expired_articles().",
    "0008_seed_nepali_sources.sql": "inserts 27 outlet rows; no destructive DDL",
    "0009_article_sources_constraint.sql": "adds missing unique constraints on "
        "article_sources + atomic increment_article_source_count()",
}


def cmd_plan() -> int:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        size = path.stat().st_size
        print(f"  {path.name}  ({size:,} bytes)")
        print(f"      {NOTES.get(path.name, '(no note)')}")
    return 0


# ---------------------------------------------------------------------
# APPLY
# ---------------------------------------------------------------------

def cmd_apply(token: str, ref: str, only: list[str] | None = None) -> int:
    # Create the ledger first so a half-applied set is visible.
    try:
        run_sql(
            token,
            ref,
            """
            create table if not exists _applied_migrations (
                filename text primary key,
                applied_at timestamptz not null default now()
            );
            """,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Could not create migration ledger: {str(exc)[:200]}")
        return 2

    already = {
        row["filename"]
        for row in run_sql(token, ref, "select filename from _applied_migrations;")
    }

    applied, skipped = 0, 0

    for path in sorted(MIGRATIONS.glob("*.sql")):
        name = path.name

        if only and not any(token_ in name for token_ in only):
            continue

        if name in already:
            print(f"  skip   {name} (already applied)")
            skipped += 1
            continue

        sql = path.read_text(encoding="utf-8")
        print(f"  apply  {name} ...", flush=True)

        try:
            run_sql(token, ref, sql, timeout=600)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL   {name}\n         {str(exc)[:400]}")
            print("\nStopped. Fix the error and re-run; already-applied files are skipped.")
            return 3

        # Record it separately so a ledger failure cannot roll back DDL.
        try:
            run_sql(
                token,
                ref,
                "insert into _applied_migrations (filename) values (%s) "
                "on conflict (filename) do nothing;"
                % ("'" + name.replace("'", "''") + "'",),
            )
        except Exception:
            print(f"         (applied, but could not record in ledger: {name})")

        applied += 1
        print(f"  OK     {name}")

    print(f"\napplied={applied} skipped={skipped}")
    return 0


# ---------------------------------------------------------------------

def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    only = sys.argv[2:] if len(sys.argv) > 2 else None

    if mode == "plan":
        return cmd_plan()

    env = load_env(ENV_FILE)
    url = env.get("NEXT_PUBLIC_SUPABASE_URL", "")
    token = env.get("API_ACCESS_TOKEN", "")

    if not url or not token:
        print("Need NEXT_PUBLIC_SUPABASE_URL and API_ACCESS_TOKEN in apps/web/.env.local")
        return 1

    ref = project_ref(url)

    try:
        if mode == "status":
            return cmd_status(token, ref)
        if mode == "apply":
            return cmd_apply(token, ref, only)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}")
        return 2

    print(f"unknown mode: {mode}  (expected status | plan | apply)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
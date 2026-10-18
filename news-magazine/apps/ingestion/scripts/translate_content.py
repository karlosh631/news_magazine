"""
Machine-translate headlines and summary points between Nepali and English.

Runs as a scheduled job, not in the ingestion hot path. It reads the
articles the pipeline just ingested and adds a translated copy alongside
the original.

THE HONEST CAVEAT, WHICH THE SCHEMA ENFORCES
--------------------------------------------
A machine translation is not the outlet's reporting. Showing one under a
real outlet's name without saying so is the exact fabrication failure
this project exists to avoid. So:

    - The original headline and summary_points are NEVER overwritten.
      They stay exactly as the outlet published them.
    - Translations are written to separate *_translated columns.
    - The rendered page always shows the original by default. The
      translation appears only when the reader asks for it, labelled
      "Machine translation" and attributed to the translator, not the
      outlet.

Skipped for any article where the API call fails or returns something
that is not a clean translation. A missing translation is fine; a wrong
one is not.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

INGESTION_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(INGESTION_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(INGESTION_ROOT / ".env")

import httpx  # noqa: E402

from app.database import get_db  # noqa: E402

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-flash-latest:generateContent"
)

# Never translate these: proper nouns, or the text is already English.
NE_PUNCT = r"[।॥]"

MAX_CONCURRENCY = 4
BATCH_SIZE = 15

SYSTEM_PROMPT = {
    "ne->en": (
        "You translate Nepali news headlines into English. "
        "Return a JSON array of strings, one per input, in the same order. "
        "Translate meaning, not word order. Keep proper nouns "
        "(place names, people, parties, institutions) in their commonly "
        "used English form. Keep names of numbers and dates where they "
        "carry meaning. Output nothing except the JSON array."
    ),
    "en->ne": (
        "You translate English news headlines into Nepali (Devanagari). "
        "Return a JSON array of strings, one per input, in the same order. "
        "Use natural journalistic Nepali, not a literal word-for-word "
        "rendering. Keep proper nouns that Nepali media normally leaves "
        "in English. Use the Nepali danda (।) as the sentence terminator. "
        "Output nothing except the JSON array."
    ),
}


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("Gemini_API_Key")


async def translate_batch(
    client: httpx.AsyncClient, texts: list[str], direction: str
) -> list[str | None]:
    """Translate one batch. Returns None per item on any failure."""
    if not texts:
        return []

    joined = "\n".join(f"{i + 1}. {text}" for i, text in enumerate(texts))
    prompt = f"{SYSTEM_PROMPT[direction]}\n\n{joined}"

    try:
        response = await client.post(
            GEMINI_ENDPOINT,
            params={"key": api_key()},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "responseMimeType": "application/json",
                },
            },
            timeout=45.0,
        )
        response.raise_for_status()
        raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(re.sub(r"```json|```", "", raw).strip())
    except Exception:
        return [None] * len(texts)

    if not isinstance(parsed, list) or len(parsed) != len(texts):
        return [None] * len(texts)

    out: list[str | None] = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        else:
            out.append(None)
    return out


def looks_nepali(text: str) -> bool:
    return bool(re.search(NE_PUNCT, text)) or bool(re.search(r"[ऀ-ॿ]", text))


async def main() -> int:
    key = api_key()
    if not key:
        print("GEMINI_API_KEY is not set; nothing to do.")
        return 0

    if os.getenv("ENABLE_TRANSLATION", "false").lower() != "true":
        print(
            "Translation is off by default. It is a paid API call and the "
            "output is machine-generated, so it stays opt-in. Set "
            "ENABLE_TRANSLATION=true to run it."
        )
        return 0

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Only recent, untranslated rows — the 48h TTL bounds the set anyway.
    try:
        response = db.rpc(
            "latest_articles", {"p_limit": 400, "p_category": None, "p_exclude_ids": None}
        ).execute()
        candidates = response.data or []
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read articles: {exc}")
        return 2

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    written = skipped = failed = 0

    async with httpx.AsyncClient() as client:

        async def process(row: dict) -> None:
            nonlocal written, skipped, failed

            headline = row.get("headline") or ""
            points = row.get("summary_points") or []
            if not headline:
                return

            # Direction is decided by what the outlet actually published.
            direction = "ne->en" if looks_nepali(headline) else "en->ne"
            texts = [headline, *[p for p in points[:4] if isinstance(p, str)]]

            async with semaphore:
                results = await translate_batch(client, texts, direction)

            translated_headline = results[0]
            if not translated_headline:
                failed += 1
                return

            translated_points = [
                r for r in results[1:] if r
            ][:4]

            payload = {
                "headline_translated": translated_headline,
                "summary_points_translated": translated_points or None,
                "translation_language": "en" if direction == "ne->en" else "ne",
                "translated_at": now,
                # Recorded so the UI can label it as machine-generated.
                "translated_by": "gemini",
            }

            try:
                db.table("articles").update(payload).eq("id", row["id"]).execute()
                written += 1
            except Exception:
                # The columns come from migration 0011. Without it, skip
                # rather than fail the whole run.
                failed += 1
                if failed == 1:
                    print(
                        "Could not write translations. Apply migration "
                        "0011_translation_columns.sql first."
                    )

        for start in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[start : start + BATCH_SIZE]
            await asyncio.gather(*(process(row) for row in batch))
            print(f"  processed {min(start + BATCH_SIZE, len(candidates))}/{len(candidates)}")

    print(f"\ntranslated={written} skipped={skipped} failed={failed}")
    return 0 if written or not candidates else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
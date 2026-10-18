/**
 * On-demand resurrection client.
 *
 * Calls the Python ingestion service's POST /api/scrape-on-demand. That
 * endpoint re-polls every enabled source when the live database has too
 * few matches for a query, re-inserts anything it finds with
 * resurrected_at set, and returns the combined results.
 *
 * The ingestion secret is attached here, server-side, and never reaches
 * the browser. The Python side treats a request carrying it as a trusted
 * caller, so the per-query hourly rate limit does not apply to the search
 * page — that limit exists to stop an anonymous script, not a user
 * searching.
 */

import "server-only";

import type { SearchResult } from "@/types/database";

export interface ResurrectionOutcome {
  articles: SearchResult[];
  resurrectedCount: number;
  rateLimited: boolean;
  note: string | null;
  available: boolean;
}

export async function triggerResurrection(
  query: string,
  limit = 24
): Promise<ResurrectionOutcome | null> {
  const baseUrl = process.env.INGESTION_SERVICE_URL;
  const secret = process.env.INGESTION_SECRET;

  // Not configured: the search page simply shows whatever the database
  // has. Do not error — most self-hosted setups have not deployed the
  // worker yet.
  if (!baseUrl || !secret) return null;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);

  try {
    const response = await fetch(`${baseUrl.replace(/\/$/, "")}/api/scrape-on-demand`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-ingestion-secret": secret,
      },
      body: JSON.stringify({ query, limit, scrape: true }),
      signal: controller.signal,
      cache: "no-store",
    });

    if (!response.ok) {
      console.error("[resurrection] service returned", response.status);
      return null;
    }

    const payload = (await response.json()) as {
      articles?: SearchResult[];
      resurrected_count?: number;
      rate_limited?: boolean;
      note?: string | null;
    };

    return {
      articles: payload.articles ?? [],
      resurrectedCount: payload.resurrected_count ?? 0,
      rateLimited: payload.rate_limited ?? false,
      note: payload.note ?? null,
      available: true,
    };
  } catch (error) {
    // A dead or unreachable ingestion service must never break search.
    console.error("[resurrection] service unreachable:", error);
    return null;
  } finally {
    clearTimeout(timeout);
  }
}
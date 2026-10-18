import { NextResponse } from "next/server";
import { z } from "zod";

import { searchArticles } from "@/lib/news";

/**
 * AI news brief.
 *
 * Gemini summarises articles that the database actually returned for the
 * query. It is a presentation layer over real search results, never a
 * substitute for them.
 *
 * Stage 1 asked Gemini to answer the question and shipped the answer with
 * no grounding: it loaded the newest 100 articles, substring-matched, and
 * when nothing matched it passed the 8 most recent articles to Gemini and
 * asked for a brief "on the topic". Searching "tesla" returned a summary
 * of unrelated Nepali politics. It also ran unauthenticated and
 * unthrottled, so anyone could spend the API key at will.
 *
 * Rules enforced here:
 *   - No search matches -> say so. Never substitute other articles.
 *   - The brief is built ONLY from rows the search returned.
 *   - Every source used is listed with its real article URL.
 *   - Without a Gemini key the raw search results are still returned.
 */

export const dynamic = "force-dynamic";

const requestSchema = z.object({
  query: z.string().trim().min(1).max(200),
  limit: z.number().int().min(1).max(20).default(8),
});

const geminiResponseSchema = z.object({
  summary: z.string().max(2000),
  key_points: z.array(z.string().max(400)).max(6),
  confidence: z.enum(["high", "medium", "low"]),
});

const GEMINI_ENDPOINT =
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent";

// Per-process rate limit so this endpoint cannot be used to drain the
// Gemini quota.
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 20;
let hits: number[] = [];

function rateLimited(): boolean {
  const now = Date.now();
  hits = hits.filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
  if (hits.length >= RATE_LIMIT_MAX) return true;
  hits.push(now);
  return false;
}

type ArticleContext = {
  headline: string;
  excerpt: string | null;
  summary_points: string[] | null;
  source_article_url: string | null;
  source_name_snapshot: string | null;
  published_at: string | null;
};

function extractText(payload: unknown): string {
  const candidate = payload as {
    candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>;
  };
  return candidate.candidates?.[0]?.content?.parts?.[0]?.text?.trim() ?? "";
}

function cleanNewsText(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, "")
    .replace(/[`*_#>-]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Straight list of the matched articles, with no AI involved. */
function noBrief(query: string, reason: string, articles: ArticleContext[] = []) {
  return NextResponse.json({
    query,
    summary: reason,
    key_points: [] as string[],
    confidence: "low" as const,
    articles,
    sources: articles.map((a) => ({
      headline: a.headline,
      url: a.source_article_url,
      source: a.source_name_snapshot,
      published_at: a.published_at,
    })),
  });
}

export async function POST(request: Request) {
  const parsedBody = requestSchema.safeParse(
    await request.json().catch(() => ({ query: "" }))
  );
  if (!parsedBody.success) {
    return NextResponse.json(
      { error: "Enter at least one search character." },
      { status: 400 }
    );
  }

  const { query, limit } = parsedBody.data;

  if (rateLimited()) {
    return NextResponse.json(
      { error: "Too many requests. Try again in a minute." },
      { status: 429 }
    );
  }

  // ---- 1. Real search first ----
  const result = await searchArticles(query, Math.max(limit, 8));

  if (result.error) {
    return NextResponse.json(
      { error: "Search is temporarily unavailable." },
      { status: 503 }
    );
  }

  const articles: ArticleContext[] = (result.articles ?? []).map((row) => ({
    headline: row.headline,
    excerpt: row.excerpt,
    summary_points: row.summary_points,
    source_article_url: row.source_article_url,
    source_name_snapshot: row.source_name_snapshot,
    published_at: row.published_at,
  }));

  // ---- 2. No matches means no brief ----
  if (articles.length === 0) {
    return noBrief(
      query,
      `No published articles match "${query}". Stories older than 48 hours are pruned — try the Search page, which re-scrapes the sources to bring an old story back.`
    );
  }

  const apiKey = process.env.GEMINI_API_KEY || process.env.Gemini_API_Key;
  if (!apiKey) {
    // Degrade to the raw results rather than 503. Search still works.
    return noBrief(query, `${articles.length} matching article(s).`, articles);
  }

  // ---- 3. Brief, grounded strictly in what search returned ----
  const prompt = [
    "You are a news desk editor for a Nepal-focused publication.",
    "Write a brief about the search topic using ONLY the numbered articles below.",
    "Hard rules:",
    "- Do not state any fact that is not present in the supplied articles.",
    "- Do not add names, figures, dates or context that the articles do not contain.",
    "- If the coverage is thin or conflicting, say so plainly.",
    "- If the articles do not actually address the topic, say so plainly.",
    "- Return JSON only. No markdown, no code fences, no commentary.",
    'Shape: {"summary": string, "key_points": string[], "confidence": "high"|"medium"|"low"}',
    `Search topic: ${query}`,
    `Matched articles:\n${articles
      .map(
        (a, i) =>
          `[${i + 1}] ${a.headline}\n${
            a.summary_points?.length
              ? a.summary_points.map((p) => `   - ${p}`).join("\n")
              : a.excerpt ?? "No summary available."
          }\nSource: ${a.source_name_snapshot ?? "unknown"}`
      )
      .join("\n\n")}`,
  ].join("\n\n");

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);

  try {
    const response = await fetch(`${GEMINI_ENDPOINT}?key=${encodeURIComponent(apiKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contents: [{ parts: [{ text: prompt }] }],
        generationConfig: { temperature: 0.1, responseMimeType: "application/json" },
      }),
      signal: controller.signal,
      cache: "no-store",
    });

    if (!response.ok) {
      console.error("[ai-search] Gemini returned", response.status);
      return noBrief(query, `${articles.length} matching article(s).`, articles);
    }

    const parsed = geminiResponseSchema.safeParse(
      JSON.parse(extractText(await response.json()))
    );
    if (!parsed.success) {
      return noBrief(query, `${articles.length} matching article(s).`, articles);
    }

    return NextResponse.json({
      query,
      summary: cleanNewsText(parsed.data.summary),
      key_points: parsed.data.key_points.map(cleanNewsText),
      confidence: parsed.data.confidence,
      articles,
      sources: articles.map((a) => ({
        headline: a.headline,
        url: a.source_article_url,
        source: a.source_name_snapshot,
        published_at: a.published_at,
      })),
    });
  } catch (error) {
    console.error("[ai-search] Gemini request failed:", error);
    return noBrief(query, `${articles.length} matching article(s).`, articles);
  } finally {
    clearTimeout(timeout);
  }
}
import { NextResponse } from "next/server";
import { z } from "zod";

import { searchArticles } from "@/lib/news";
import { triggerResurrection } from "@/lib/resurrection";

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

/**
 * Google-search grounding.
 *
 * The database only holds the last 48 hours, so a search for anything
 * older returns nothing. Rather than leave the reader with a dead end,
 * fall back to Gemini with the `googleSearch` grounding tool, which
 * retrieves real web results and returns them WITH the source URLs it
 * actually retrieved.
 *
 * This is the only safe way to extend search past the retention window
 * from inside Vercel: the answers are grounded in retrieved pages, each
 * result carries the URL it came from, and the model is explicitly
 * forbidden from stating anything that is not in those pages. That is
 * categorically different from Stage 1, which asked Gemini to invent
 * headlines and rendered them as news.
 */
async function groundedSearch(query: string): Promise<{
  answer: string | null;
  sources: Array<{ title: string; url: string }>;
} | null> {
  const apiKey = process.env.GEMINI_API_KEY || process.env.Gemini_API_Key;
  if (!apiKey) return null;

  const prompt = [
    `Search the web for recent Nepali and international news about: ${query}`,
    "",
    "Report only what the retrieved pages actually say.",
    "For each distinct story, give a one-sentence factual summary and the source URL.",
    "Do not invent facts, dates, or outlets. If the retrieved pages do not",
    "cover the topic, say so plainly.",
    "",
    'Return JSON only: {"answer": string, "stories": [{"headline": string, "url": string, "summary": string}]}',
  ].join("\n");

  try {
    const response = await fetch(`${GEMINI_ENDPOINT}?key=${encodeURIComponent(apiKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contents: [{ parts: [{ text: prompt }] }],
        tools: [{ google_search: {} }],
        generationConfig: { temperature: 0.1, responseMimeType: "application/json" },
      }),
      signal: AbortSignal.timeout(20_000),
      cache: "no-store",
    });

    if (!response.ok) return null;

    const payload = await response.json();
    const text = extractText(payload);
    const parsed = JSON.parse(
      text.replace(/```json|```/g, "").trim()
    ) as {
      answer?: string;
      stories?: Array<{ headline?: string; url?: string; summary?: string }>;
    };

    // Grounding metadata lists what was actually retrieved. Prefer it
    // for the source list so a URL cannot be hallucinated into the
    // citations block.
    const grounding: Array<{ title?: string; uri?: string }> =
      payload?.candidates?.[0]?.groundingMetadata?.groundingChunks ?? [];

    const grounded = grounding
      .filter((chunk) => chunk.uri)
      .map((chunk) => ({ title: chunk.title ?? chunk.uri!, url: chunk.uri! }));

    const stories: Array<{ title: string; url: string }> = (parsed.stories ?? [])
      .filter((s) => s && typeof s.url === "string" && /^https?:\/\//.test(s.url))
      .map((s) => ({
        title: String(s.headline ?? "").trim(),
        url: s.url as string,
      }))
      .filter((s) => s.title.length > 0);

    return {
      answer: typeof parsed.answer === "string" ? cleanNewsText(parsed.answer) : null,
      sources: grounded.length >= stories.length && grounded.length > 0 ? grounded : stories,
    };
  } catch {
    return null;
  }
}

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

  // ---- 2. No matches: try to recover the story ----
  if (articles.length === 0) {
    // (a) The Python service may re-scrape the configured outlets for a
    //     pruned story. Best fidelity: real article, real URL.
    const resurrected = await triggerResurrection(query, 5);
    if (resurrected && resurrected.articles.length > 0) {
      return NextResponse.json({
        query,
        summary: `${resurrected.articles.length} matching article(s) re-fetched from source.`,
        key_points: [],
        confidence: "medium" as const,
        articles: resurrected.articles,
        sources: resurrected.articles.map((a) => ({
          headline: a.headline,
          url: a.source_article_url,
          source: a.source_name_snapshot,
          published_at: a.published_at,
        })),
        resurrected: true,
      });
    }

    // (b) Fall back to Google-search grounding, which works entirely
    //     inside Vercel and needs no Python service deployed.
    const grounded = await groundedSearch(query);
    if (grounded && grounded.sources.length > 0) {
      return NextResponse.json({
        query,
        summary:
          grounded.answer ??
          `Nothing published here in the last 48 hours. These are live web results.`,
        key_points: [],
        confidence: "low" as const,
        articles: [],
        sources: grounded.sources,
        grounded: true,
        note: "Not from our archive — these are live web results with their original sources.",
      });
    }

    // Both fallbacks declined. Say which reason, because "nothing
    // matches" reads as "this story does not exist" when the real cause
    // may just be that no API key is configured.
    const noKey =
      !process.env.GEMINI_API_KEY && !process.env.Gemini_API_Key;

    return noBrief(
      query,
      noKey
        ? `No published articles match "${query}", and live web fallback is disabled because GEMINI_API_KEY is not set. Set it on Vercel to have Gemini search the web when a query finds nothing.`
        : `No published articles match "${query}" in the last 48 hours, and the live web search found nothing either.`
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
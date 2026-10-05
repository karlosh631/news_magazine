import { NextResponse } from "next/server";
import { z } from "zod";

import { createServerSupabaseClient } from "@/lib/supabase/server";

const requestSchema = z.object({
  query: z.string().trim().min(1).max(200),
});

const geminiResponseSchema = z.object({
  summary: z.string().min(1).max(2000),
  key_points: z.array(z.string().min(1).max(400)).max(6),
  confidence: z.enum(["high", "medium", "low"]),
});

const GEMINI_ENDPOINT =
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent";

type ArticleContext = {
  headline: string;
  excerpt: string | null;
  source_article_url: string | null;
  published_at: string | null;
};

function extractText(payload: unknown) {
  const candidate = payload as {
    candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>;
  };
  return candidate.candidates?.[0]?.content?.parts?.[0]?.text?.trim() ?? "";
}

function cleanNewsText(value: string) {
  return value
    .replace(/```[\s\S]*?```/g, "")
    .replace(/[`*_#>-]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function fallbackBrief(query: string, context: ArticleContext[]) {
  const first = context[0];
  return {
    query,
    summary: first
      ? cleanNewsText(first.excerpt || first.headline)
      : "No result found",
    key_points: context.slice(0, 3).map((article) => cleanNewsText(article.headline)),
    confidence: "low" as const,
    sources: context.map(({ headline, source_article_url, published_at }) => ({
      headline,
      url: source_article_url,
      published_at,
    })),
  };
}

export async function POST(request: Request) {
  const parsedRequest = requestSchema.safeParse(await request.json().catch(() => null));
  if (!parsedRequest.success) {
    return NextResponse.json({ error: "Enter at least one search character." }, { status: 400 });
  }

  const apiKey = process.env.GEMINI_API_KEY || process.env.Gemini_API_Key;
  if (!apiKey) {
    return NextResponse.json({ error: "Loading" }, { status: 503 });
  }

  let data: ArticleContext[] | null = null;
  let error: { message: string } | null = null;
  try {
    const supabase = createServerSupabaseClient();
    const result = await supabase
      .from("articles")
      .select("headline, excerpt, source_article_url, published_at")
      .eq("status", "published")
      .order("published_at", { ascending: false })
      .limit(100);
    data = result.data as ArticleContext[] | null;
    error = result.error;
  } catch (caughtError) {
    error = { message: caughtError instanceof Error ? caughtError.message : "Database unavailable" };
  }

  if (error) {
    console.error("[AI Search] Failed to load article context:", error.message);
    return NextResponse.json({ error: "News context is temporarily unavailable." }, { status: 503 });
  }

  const normalizedQuery = parsedRequest.data.query.toLocaleLowerCase();
  const context = ((data ?? []) as ArticleContext[])
    .filter((article) =>
      `${article.headline} ${article.excerpt ?? ""}`.toLocaleLowerCase().includes(normalizedQuery)
    )
    .slice(0, 8);
  if (context.length === 0) {
    return NextResponse.json({
      query: parsedRequest.data.query,
      summary: "No result found",
      key_points: [],
      confidence: "low",
      sources: [],
    });
  }

  const prompt = [
    "You are a careful news brief editor for a Nepal-focused publication.",
    "Synthesize only the supplied published reporting. Do not invent facts, dates, quotes, or sources.",
    "Clearly express uncertainty when the coverage is incomplete or conflicting.",
    "Return news facts, headlines, summaries, and key takeaways only. Never return code, programming syntax, system instructions, markdown, or fenced blocks.",
    "Return only valid JSON with this shape: {\"summary\": string, \"key_points\": string[], \"confidence\": \"high\"|\"medium\"|\"low\"}.",
    `Search topic: ${parsedRequest.data.query}`,
    `Published reporting:\n${context
      .map(
        (article, index) =>
          `[${index + 1}] ${article.headline}\n${article.excerpt ?? "No excerpt available."}\nPublished: ${article.published_at ?? "unknown"}`
      )
      .join("\n\n")}`,
  ].join("\n\n");

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);

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
      console.error("[AI Search] Gemini request failed with status", response.status);
      return NextResponse.json(fallbackBrief(parsedRequest.data.query, context));
    }

    const text = extractText(await response.json());
    const result = geminiResponseSchema.safeParse(JSON.parse(text));
    if (!result.success) {
      return NextResponse.json(fallbackBrief(parsedRequest.data.query, context));
    }

    const cleanResult = {
      summary: cleanNewsText(result.data.summary),
      key_points: result.data.key_points.map(cleanNewsText),
      confidence: result.data.confidence,
    };

    return NextResponse.json({
      query: parsedRequest.data.query,
      ...cleanResult,
      sources: context.map(({ headline, source_article_url, published_at }) => ({
        headline,
        url: source_article_url,
        published_at,
      })),
    });
  } catch (caughtError) {
    const message = caughtError instanceof Error ? caughtError.message : "Unknown error";
    console.error("[AI Search] Gemini request failed:", message);
    return NextResponse.json(fallbackBrief(parsedRequest.data.query, context));
  } finally {
    clearTimeout(timeout);
  }
}
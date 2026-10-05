import { NextResponse } from "next/server";
import { z } from "zod";

import { createServerSupabaseClient } from "@/lib/supabase/server";

const requestSchema = z.object({
  query: z.string().trim().min(2).max(200),
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

export async function POST(request: Request) {
  const parsedRequest = requestSchema.safeParse(await request.json().catch(() => null));
  if (!parsedRequest.success) {
    return NextResponse.json({ error: "Enter a search query with at least 2 characters." }, { status: 400 });
  }

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: "AI search is not configured." }, { status: 503 });
  }

  const supabase = createServerSupabaseClient();
  const { data, error } = await supabase
    .from("articles")
    .select("headline, excerpt, source_article_url, published_at")
    .eq("status", "published")
    .order("published_at", { ascending: false })
    .limit(100);

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
      summary: "No published coverage matched this topic yet.",
      key_points: [],
      confidence: "low",
      sources: [],
    });
  }

  const prompt = [
    "You are a careful news brief editor for a Nepal-focused publication.",
    "Synthesize only the supplied published reporting. Do not invent facts, dates, quotes, or sources.",
    "Clearly express uncertainty when the coverage is incomplete or conflicting.",
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
      return NextResponse.json({ error: "AI search is temporarily unavailable." }, { status: 502 });
    }

    const text = extractText(await response.json());
    const result = geminiResponseSchema.safeParse(JSON.parse(text));
    if (!result.success) {
      return NextResponse.json({ error: "AI returned an invalid news brief." }, { status: 502 });
    }

    return NextResponse.json({
      query: parsedRequest.data.query,
      ...result.data,
      sources: context.map(({ headline, source_article_url, published_at }) => ({
        headline,
        url: source_article_url,
        published_at,
      })),
    });
  } catch (caughtError) {
    const message = caughtError instanceof Error ? caughtError.message : "Unknown error";
    console.error("[AI Search] Gemini request failed:", message);
    return NextResponse.json({ error: "AI search timed out. Try again shortly." }, { status: 504 });
  } finally {
    clearTimeout(timeout);
  }
}
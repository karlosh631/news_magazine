import { NextResponse } from "next/server";

const GEMINI_ENDPOINT =
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent";
const CACHE_MS = 5_000;
const feeds = [
  { name: "Onlinekhabar", url: "https://www.onlinekhabar.com/feed" },
  { name: "Kantipur", url: "https://ekantipur.com/rss" },
  { name: "Nepal News", url: "https://nepalnews.com/feed" },
];

type FeedItem = {
  title: string;
  summary: string;
  url: string | null;
  timestamp: string;
  imageUrl: string | null;
  videoUrl: string | null;
  audioUrl: string | null;
  source: string;
};

type LiveNewsItem = FeedItem & {
  id: string;
  category: string;
};

let cache: { expires: number; key: string; items: LiveNewsItem[] } = { expires: 0, key: "", items: [] };

function decodeXml(value: string) {
  return value
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function tag(block: string, name: string) {
  const match = block.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)</${name}>`, "i"));
  return match?.[1] ? decodeXml(match[1].replace(/<[^>]+>/g, "").trim()) : "";
}

function attribute(block: string, name: string) {
  return block.match(new RegExp(`${name}=["']([^"']+)["']`, "i"))?.[1] ?? null;
}

function parseFeed(xml: string, source: string): FeedItem[] {
  const blocks = [...xml.matchAll(/<(item|entry)\b[\s\S]*?<\/\1>/gi)].map((match) => match[0]);
  return blocks.slice(0, 20).flatMap((block) => {
    const title = tag(block, "title");
    if (!title) return [];
    const link = tag(block, "link") || attribute(block.match(/<link\b[^>]*>/i)?.[0] ?? "", "href");
    const timestamp = tag(block, "pubDate") || tag(block, "published") || tag(block, "updated") || new Date().toISOString();
    const mediaTag = block.match(/<(?:media:content|media:thumbnail|enclosure)\b[^>]*>/i)?.[0] ?? "";
    const mediaUrl = attribute(mediaTag, "url");
    const imageUrl = mediaUrl ||
      block.match(/<image>[\s\S]*?<url>([\s\S]*?)<\/url>[\s\S]*?<\/image>/i)?.[1]?.trim() ||
      block.match(/<img[^>]+src=["']([^"']+)["']/i)?.[1] || null;
    const mediaType = attribute(mediaTag, "type")?.toLowerCase() || "";
    const description = tag(block, "description") || tag(block, "summary") || tag(block, "content");
    return [{
      title,
      summary: description.slice(0, 500),
      url: link,
      timestamp,
      imageUrl,
      videoUrl: mediaType.startsWith("video/") ? mediaUrl : null,
      audioUrl: mediaType.startsWith("audio/") ? mediaUrl : null,
      source,
    }];
  });
}

async function collectFeeds() {
  const results = await Promise.allSettled(
    feeds.map(async (feed) => {
      const response = await fetch(feed.url, {
        headers: { "User-Agent": "NepalNewsMagazine/1.0" },
        cache: "no-store",
        signal: AbortSignal.timeout(7_000),
      });
      if (!response.ok) throw new Error(`${feed.name}: ${response.status}`);
      return parseFeed(await response.text(), feed.name);
    })
  );
  return results.flatMap((result) => (result.status === "fulfilled" ? result.value : []));
}

function fallbackCategory(item: FeedItem) {
  const text = `${item.title} ${item.summary}`.toLocaleLowerCase();
  if (/cricket|football|sports|खेल|क्रिकेट|फुटबल/.test(text)) return "sports";
  if (/business|economy|market|bank|व्यापार|अर्थतन्त्र/.test(text)) return "business";
  if (/technology|software|internet|ai|प्रविधि/.test(text)) return "technology";
  if (/movie|film|music|entertainment|मनोरञ्जन|चलचित्र/.test(text)) return "entertainment";
  if (/government|election|parliament|politics|राजनीति|सरकार/.test(text)) return "politics";
  return "national";
}

function fallbackItems(items: FeedItem[]) {
  return items.map((item, index) => ({
    ...item,
    id: `live-${index}-${Buffer.from(item.title).toString("base64url").slice(0, 12)}`,
    category: fallbackCategory(item),
  }));
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const category = (url.searchParams.get("category") || "all").toLowerCase();
  const language = url.searchParams.get("language") === "ne" ? "Nepali" : "English";
  const cacheKey = `${category}:${language}`;

  if (cache.expires > Date.now() && cache.key === cacheKey) {
    const filtered = category === "all" ? cache.items : cache.items.filter((item) => item.category === category);
    return NextResponse.json(filtered);
  }

  const collected = await collectFeeds();
  if (collected.length === 0) {
    cache = { expires: Date.now() + CACHE_MS, key: cacheKey, items: [] };
    return NextResponse.json([]);
  }

  const apiKey = process.env.GEMINI_API_KEY || process.env.Gemini_API_Key;
  let items = fallbackItems(collected);
  if (apiKey) {
    try {
      const prompt = `Synthesize these RSS reports into the latest factual ${language} news. Return JSON only: {"stories":[{"title":"string","summary":"string","category":"national|politics|business|technology|sports|entertainment","timestamp":"ISO string","imageUrl":"string|null","videoUrl":"string|null","audioUrl":"string|null","source":"string","url":"string|null"}]}. Never return markdown, code, instructions, or invented media URLs.\nREPORTS:\n${collected.map((item) => `${item.title}\n${item.summary}\n${item.url ?? ""}\n${item.source}`).join("\n\n")}`;
      const response = await fetch(`${GEMINI_ENDPOINT}?key=${encodeURIComponent(apiKey)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }], generationConfig: { temperature: 0.1, responseMimeType: "application/json" } }),
        cache: "no-store",
        signal: AbortSignal.timeout(12_000),
      });
      const payload = await response.json();
      const text = payload?.candidates?.[0]?.content?.parts?.[0]?.text;
      const stories = text ? JSON.parse(text.replace(/```json|```/g, "").trim()).stories : null;
      if (Array.isArray(stories)) {
        items = stories.filter((story: unknown) => story && typeof story === "object" && typeof (story as { title?: unknown }).title === "string").map((story: Record<string, unknown>, index: number) => ({
          id: `gemini-live-${index}-${Date.now()}`,
          title: String(story.title),
          summary: typeof story.summary === "string" ? story.summary : "",
          category: typeof story.category === "string" ? story.category : "national",
          timestamp: typeof story.timestamp === "string" ? story.timestamp : new Date().toISOString(),
          imageUrl: typeof story.imageUrl === "string" ? story.imageUrl : null,
          videoUrl: typeof story.videoUrl === "string" ? story.videoUrl : null,
          audioUrl: typeof story.audioUrl === "string" ? story.audioUrl : null,
          source: typeof story.source === "string" ? story.source : "Live RSS",
          url: typeof story.url === "string" ? story.url : null,
        }));
      }
    } catch (error) {
      console.error("[live-news] Gemini synthesis failed:", error);
    }
  }

  cache = { expires: Date.now() + CACHE_MS, key: cacheKey, items };
  return NextResponse.json(category === "all" ? items : items.filter((item) => item.category === category));
}

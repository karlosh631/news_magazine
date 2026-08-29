import os
import json
import re
import anthropic
from typing import Dict, Any, List

SECTORS = ["Coding", "Hackathons", "Nepal Top News", "World News", "IT"]

def generate_ieee_publication(sector: str, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Takes raw scraped articles from your Python pipeline and uses Anthropic Claude
    to write an IEEE standard publication.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is missing.")

    client = anthropic.Anthropic(api_key=api_key)

    # Format scraped articles into input context for Claude
    context_blocks = []
    for idx, art in enumerate(articles[:5], start=1):
        context_blocks.append(
            f"[{idx}] Title: {art.get('title', 'N/A')}\n"
            f"Source URL: {art.get('url', 'N/A')}\n"
            f"Summary/Content: {art.get('content', art.get('summary', 'N/A'))[:500]}"
        )
    context_str = "\n\n".join(context_blocks)

    prompt = f"""You are a distinguished IEEE Senior Fellow and technical research journalist.
Synthesize the following real-time scraped news for the sector "{sector}" into a formal IEEE standard publication.

Scraped Context:
{context_str}

REQUIREMENTS:
1. Return ONLY a valid JSON object matching this strict schema:
{{
  "title": "A precise, technical title without quotes",
  "abstract": "A continuous 150-250 word formal technical abstract summarizing the problem, developments, and implications.",
  "content_ieee": "Markdown formatted IEEE standard body containing exactly these 5 sections:\n\n# I. INTRODUCTION\n...\n\n# II. TECHNICAL LANDSCAPE & METHODOLOGY\n...\n\n# III. SYSTEM ARCHITECTURE & IMPLEMENTATION\n...\n\n# IV. EVALUATION & DISCUSSIONS\n...\n\n# V. CONCLUSION & FUTURE DIRECTIONS\n...",
  "references": [
    {{
      "id": 1,
      "citation": "Author et al., \\"Title of reference\\", Source, 2026.",
      "url": "https://..."
    }}
  ]
}}

2. Embed numerical citations matching the references array like [1], [2] inside content_ieee.
3. Return ONLY raw JSON without markdown backticks or conversational text."""

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw_text = response.content[0].text.strip()
    
    # Sanitize markdown code blocks if present
    cleaned_json = re.sub(r"^```json\s*", "", raw_text)
    cleaned_json = re.sub(r"```$", "", cleaned_json).strip()

    return json.loads(cleaned_json)

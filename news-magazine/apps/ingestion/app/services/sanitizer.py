"""
Content sanitization helpers.

RSS/Atom content is untrusted input.

Rules:
- Never render raw feed HTML as trusted application HTML.
- Strip HTML from headlines and excerpts.
- Escape output before placing it inside generated HTML.
- Limit lengths to prevent oversized database content.
"""

from __future__ import annotations

import html
import re


# Conservative limits.
MAX_HEADLINE_LENGTH = 500
MAX_EXCERPT_LENGTH = 2000


def _strip_html(value: str | None) -> str:
    """
    Convert untrusted HTML/text into plain text.

    This intentionally removes all HTML instead of attempting
    to maintain an allow-list of HTML tags.
    """

    if not value:
        return ""

    text = str(value)

    # Remove script/style blocks completely.
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove comments.
    text = re.sub(
        r"<!--.*?-->",
        " ",
        text,
        flags=re.DOTALL,
    )

    # Remove remaining HTML tags.
    text = re.sub(
        r"<[^>]*>",
        " ",
        text,
    )

    # Decode entities such as:
    # &amp; &quot; &#39; &nbsp;
    text = html.unescape(text)

    # Normalize whitespace.
    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def sanitize_plain_text(
    value: str | None,
    max_length: int | None = None,
) -> str:
    """
    Return safe plain text.

    No HTML is preserved.
    """

    text = _strip_html(value)

    if max_length is not None:
        text = text[:max_length].strip()

    return text


def sanitize_headline(
    value: str | None,
) -> str:
    """
    Sanitize an article headline.
    """

    return sanitize_plain_text(
        value,
        MAX_HEADLINE_LENGTH,
    )


def sanitize_excerpt(
    value: str | None,
) -> str:
    """
    Sanitize an article excerpt/summary.
    """

    return sanitize_plain_text(
        value,
        MAX_EXCERPT_LENGTH,
    )


def build_safe_paragraph(
    value: str | None,
) -> str:
    """
    Convert untrusted text into safe HTML containing
    exactly one paragraph.

    Important:
    html.escape() happens AFTER stripping HTML so characters
    such as < and > from the original feed cannot become tags.
    """

    text = sanitize_excerpt(value)

    if not text:
        return "<p></p>"

    escaped = html.escape(
        text,
        quote=True,
    )

    return f"<p>{escaped}</p>"

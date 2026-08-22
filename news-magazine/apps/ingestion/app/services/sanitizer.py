from __future__ import annotations

import html
import re


# Remove dangerous HTML completely.
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|iframe|object|embed|form|input|textarea|button|"
    r"svg|math|link|meta)\b[^>]*>.*?</\1\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)

_TAG_RE = re.compile(
    r"<[^>]*>",
    flags=re.IGNORECASE,
)

_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"
)

_WHITESPACE_RE = re.compile(
    r"\s+"
)


def sanitize_plain_text(
    value: str | None,
    *,
    max_length: int = 5000,
) -> str:
    """
    Convert untrusted feed content into safe plain text.

    This function is intentionally conservative:
    - HTML tags are removed.
    - Script/style and dangerous embedded elements are removed.
    - HTML entities are decoded.
    - Control characters are removed.
    - Excessive whitespace is normalized.
    - Output length is capped.

    Never use this function as a replacement for an HTML sanitizer
    when intentionally storing trusted HTML.
    """

    if not value:
        return ""

    value = str(value)

    # Remove dangerous blocks before stripping ordinary tags.
    value = _SCRIPT_STYLE_RE.sub(" ", value)

    # Remove any remaining HTML markup.
    value = _TAG_RE.sub(" ", value)

    # Decode entities such as:
    # &amp; -> &
    # &nbsp; -> space
    value = html.unescape(value)

    # Remove invisible/control characters.
    value = _CONTROL_CHARS_RE.sub("", value)

    # Normalize whitespace.
    value = _WHITESPACE_RE.sub(" ", value).strip()

    if max_length > 0:
        value = value[:max_length].strip()

    return value


def sanitize_excerpt(
    value: str | None,
    *,
    max_length: int = 2000,
) -> str:
    """
    Sanitize an article/feed excerpt.
    """

    return sanitize_plain_text(
        value,
        max_length=max_length,
    )


def sanitize_headline(
    value: str | None,
    *,
    max_length: int = 500,
) -> str:
    """
    Sanitize an article headline.
    """

    return sanitize_plain_text(
        value,
        max_length=max_length,
    )


def escape_html_text(
    value: str | None,
) -> str:
    """
    Escape plain text before inserting it into generated HTML.

    Example:
        <script>alert(1)</script>

    becomes harmless HTML text.
    """

    if not value:
        return ""

    return html.escape(
        str(value),
        quote=True,
    )


def build_safe_paragraph(
    value: str | None,
) -> str:
    """
    Convert plain text into a safe paragraph.

    This is useful when the ingestion pipeline needs a minimal
    body_html value without allowing feed HTML to execute.
    """

    safe_text = sanitize_plain_text(value)

    if not safe_text:
        return "<p></p>"

    return f"<p>{escape_html_text(safe_text)}</p>"

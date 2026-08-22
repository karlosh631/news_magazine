"""
Sanitizes any HTML that came from outside the platform (imported excerpts,
and — reused by the web app's comment API — user comments) before it is
ever stored or rendered. Never trust external HTML.
"""
import bleach

ALLOWED_TAGS = ["b", "i", "em", "strong", "a", "p", "br"]
ALLOWED_ATTRS = {"a": ["href", "rel", "target"]}


def sanitize_html(raw_html: str | None) -> str | None:
    if raw_html is None:
        return None
    cleaned = bleach.clean(raw_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    return bleach.linkify(cleaned)


def sanitize_plain_text(raw: str | None) -> str | None:
    """Strip all HTML entirely — used for excerpts, headlines, etc."""
    if raw is None:
        return None
    return bleach.clean(raw, tags=[], strip=True).strip()

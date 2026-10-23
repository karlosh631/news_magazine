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



#: The chrome a listing card prints around the story: the outlet's own
#: domain, a bullet, the date and the time, and sometimes the time
#: since publication in the reader's own language.
_CARD_CHROME_RE = re.compile(
    r"[\w-]+(?:\.[\w-]+)*\.(?:com\.np|com|net|org|np|gov|edu|io)\b\s*[•|]\s*"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+\w{3,}\.?\s+\d{1,2}\s+"
    r"\d{1,2}:\d{2}:\d{2}\s+(?:GMT|UTC)\s+\d{4}",
    re.IGNORECASE,
)

_RELATIVE_CHROME_RE = re.compile(
    r"(?:^|\s)\d+\s*(?:minutes?|mins?|hours?|hrs?)\s+ago(?:\s|$)|"
    r"(?:^|\s)\d+\s*मिनेट\s*अघि(?:\s|$)|"
    r"(?:^|\s)\d+\s*घंटा\s*अघि(?:\s|$)",
    re.IGNORECASE,
)


def strip_card_chrome(text: str | None) -> str:
    """
    Take the listing card's chrome out of a piece of text.

    WHY
    ---
    A client-rendered listing card is a summary of the story, and the
    summary includes the card's own furniture: the outlet's domain, a
    bullet, the date and the time, and the time since publication. When
    the card's text was taken for the story's text, that furniture went
    into the story's four key points, and the reader saw:

        नयाँ संस्कारको नागरिक नागरिक २७ गतेदेखि himalpress.com •
        Fri Oct 23 16:05:51 GMT 2026 काठमाडौं

    The sentence after the furniture IS the outlet's own words; the
    furniture before it is not part of any sentence.

    The rule is narrow: it removes the domain-and-date string and a
    leading "x minutes ago", and nothing else. A story that mentions a
    website in its own words keeps it.
    """
    if not text:
        return ""
    cleaned = _CARD_CHROME_RE.sub(" ", text)
    cleaned = _RELATIVE_CHROME_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()

def collapse_repeated_headline(text: str) -> str:
    """
    A headline that says the same thing twice, said once.

    WHY
    ---
    Some publishers put the headline in their markup twice - once as
    the visible heading and once in a second element the desktop and
    the phone each use - and the listing parser reads the container
    rather than the heading. The stored headline then reads:

        Tanker with Russian diesel heads to New York under Trump deal
        Tanker with Russian diesel heads to New York under Trump deal

    which is what the reader sees in the search dropdown and on the
    card, and it is the outlet's headline with the outlet's own
    headline appended to it.

    The rule is deliberately strict. Every possible length of the first
    copy is tried, and the rest of the headline must be nothing BUT
    copies of it. A headline that merely mentions itself ("Man
    arrested after man arrested in theft") is news, not a bug, and is
    left alone.

    This runs on every headline on the way into the database, so the
    rows that get there are right; and the web app applies the same
    rule when it renders a row, so the rows that are ALREADY stored
    are right on screen too.
    """
    value = (text or "").strip()
    if len(value) < 20:
        return value

    for size in range(len(value) // 2, 11, -1):
        head = value[:size].strip()
        if not head:
            continue

        rest = value[size:].strip()
        if not rest:
            continue

        leftover = rest
        while leftover.startswith(head):
            leftover = leftover[len(head):].strip()
        if not leftover:
            return head

    return value


def sanitize_headline(
    value: str | None,
) -> str:
    """
    Sanitize an article headline.
    """

    return collapse_repeated_headline(
        sanitize_plain_text(
            value,
            MAX_HEADLINE_LENGTH,
        )
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

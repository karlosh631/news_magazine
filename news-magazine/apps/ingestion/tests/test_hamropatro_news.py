"""
Hamro Patro's own news section, read with the html_listing adapter.

WHAT IS ASSERTED HERE
---------------------
That the selector_config stored by migration 0030 reads the section
correctly, on the markup hamropatro.com actually serves, and that
the headline it stores is ONLY the headline.

The cards on that site are anchors of the site's own `hp-card-surface`
class, and each one carries four texts:

    1. the name of the outlet the story was taken from
       ("onlinekhabar.com")
    2. a relative timestamp in Nepali prose ("३५ मिनेट अघि")
    3. the headline, in the <h3>
    4. the summary, in the <p>

An adapter with no `title` selector falls back to the link's own text,
which on this site is all four. So the `title: h3` selector is the
whole difference between a headline and a paragraph of chrome, and
that is what these tests pin down.

The last test reads the migration file itself, so the configuration
that is verified here and the configuration that reaches the database
cannot drift apart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.sources.html_listing import HtmlListingAdapter


#: The selector_config as written in
#: supabase/migrations/0030_hamropatro_news.sql.
SELECTOR_CONFIG = {
    "item": "a.hp-card-surface[href*='/news/detail/']",
    "link": "a[href]",
    "title": "h3",
    "image": "img",
    "exclude": (
        r"/author/|/category/|/tag/|/page/\d+|"
        r"\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|mp4)$"
    ),
}

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "0030_hamropatro_news.sql"
)


def adapter(fallback_url: str = "https://www.hamropatro.com/news") -> HtmlListingAdapter:
    return HtmlListingAdapter({
        "name": "Hamro Patro News",
        "base_url": "https://www.hamropatro.com",
        "fallback_url": fallback_url,
        "feed_url": None,
        "adapter_key": "html_listing",
        "enabled": True,
        "allowed": True,
        "rate_limit_per_minute": 3,
        "default_language": "ne",
        "sector": "national",
        "selector_config": SELECTOR_CONFIG,
    })


# ---------------------------------------------------------------------------
# The markup the site actually serves, trimmed to one card of each shape.
# ---------------------------------------------------------------------------

CARD_WITH_PICTURE = """
<a class="hp-card-surface group flex gap-3 overflow-hidden p-3 outline-none transition-colors hover:border-primary"
   href="/news/detail/4924822949913971-ijarayalama-vamapanth-r-dakshinapanthako-arth-ke-ho">
  <div class="aspect-square w-20 shrink-0 overflow-hidden rounded-md bg-date-strip sm:w-24">
    <img alt="" class="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
         loading="lazy"
         src="https://image.hamropatro.com/imgs/rs:fill:200:200:1/g:ce/aHR0cHM6Ly93d3cub25saW5la2hhYmFyLmNvbS93cC1jb250ZW50L3VwbG9hZHMvMjAyMi8xMi9iZW5qYW1pbi1uZXRhbnl1LWlzcmFlbC03Njh4NDQ4LmpwZw.webp">
  </div>
  <div class="flex min-w-0 flex-1 flex-col gap-1">
    <span class="flex flex-wrap items-center gap-1.5 font-np text-label-sm text-text-secondary">
      <span class="font-semibold text-primary">onlinekhabar.com</span>
      <span aria-hidden="true">·</span>
      <span>३५ मिनेट अघि</span>
    </span>
    <h3 class="font-np font-bold text-text-primary group-hover:text-primary line-clamp-2 text-title-sm">
      इजरायलमा वामपन्थ र दक्षिणपन्थको अर्थ के हो ? विपक्षी को हो ?
    </h3>
    <p class="line-clamp-2 font-np text-body-sm text-text-secondary">
      ६ कात्तिक, काठमाडौं । इजरायलमा आगामी अक्टोबर २७ मा हुने निर्वाचनले प्रधानमन्त्री बेन्जामिन नेतन्याहुको सत्ता रहने वा नरहने भन्ने टुङ्गो लगाउनेछ ।
    </p>
  </div>
</a>
"""

CARD_WITHOUT_PICTURE = """
<li class="flex items-center gap-2 sm:gap-3">
  <span aria-hidden="true" class="w-5 shrink-0 text-center font-np text-title-md font-bold tabular-nums text-text-secondary">१</span>
  <div class="min-w-0 flex-1">
    <a class="hp-card-surface group flex gap-3 overflow-hidden p-3 outline-none transition-colors hover:border-primary"
       href="/news/detail/4926307025576464-banggaladeshaka-mohammad-saiphauddinale-pokharabat-enapiel-khelne">
      <div class="flex min-w-0 flex-1 flex-col gap-1">
        <span class="flex flex-wrap items-center gap-1.5 font-np text-label-sm text-text-secondary">
          <span class="font-semibold text-primary">ratopati.com</span>
          <span aria-hidden="true">·</span>
          <span>१० मिनेट अघि</span>
        </span>
        <h3 class="font-np font-bold text-text-primary group-hover:text-primary line-clamp-2 text-title-sm">
          बङ्गलादेशका मोहम्मद सैफउद्दिनले पोखराबाट एनपिएल खेल्ने
        </h3>
        <p class="line-clamp-2 font-np text-body-sm text-text-secondary">
          काठमाडौं । बङ्गलादेशका क्रिकेटर मोहम्मद सैफउद्दिनले नेपाल प्रिमियर लिग को दोस्रो संस्करण पोखरा एभेन्जर्सबाट खेल्ने भएका छन् ।
        </p>
      </div>
    </a>
  </div>
</li>
"""

LISTING_HTML = f"""
<html lang="ne"><body>
  <nav>
    <a href="/news/national">राष्ट्र</a>
    <a href="/news/sports">खेलकुद</a>
    <a href="/news/economics">अर्थ</a>
  </nav>
  <ul class="grid grid-cols-1 gap-4 md:grid-cols-2">
    <li>{CARD_WITH_PICTURE}</li>
    <li>{CARD_WITHOUT_PICTURE}</li>
  </ul>
</body></html>
"""


# ---------------------------------------------------------------------------
# The headline is only the headline
# ---------------------------------------------------------------------------

def test_the_headline_is_the_h3_and_nothing_else():
    """
    The anchor's own text is the outlet name, the timestamp, the
    headline and the summary. Only the <h3> is the headline.
    """
    entries = adapter().parse(LISTING_HTML)

    assert len(entries) == 2

    titles = [e["title"] for e in entries]
    assert "इजरायलमा वामपन्थ र दक्षिणपन्थको अर्थ के हो ? विपक्षी को हो ?" in titles
    assert "बङ्गलादेशका मोहम्मद सैफउद्दिनले पोखराबाट एनपिएल खेल्ने" in titles

    for title in titles:
        # none of the chrome that surrounds the headline
        assert "onlinekhabar.com" not in title
        assert "ratopati.com" not in title
        assert "मिनेट अघि" not in title
        assert "·" not in title
        # and none of the summary
        assert "काठमाडौं" not in title
        assert "निर्वाचनले" not in title


def test_without_a_title_selector_the_headline_would_be_the_whole_card():
    """
    Why the `title` selector is not optional on this site.

    The adapter falls back to the link's own text when the config
    names no title, and on this site that text is all four pieces of
    chrome at once.
    """
    config = dict(SELECTOR_CONFIG)
    del config["title"]

    # Same page, same adapter - but with no title selector the stored
    # headline is the card's whole text.
    fallback = HtmlListingAdapter({
        "name": "Hamro Patro News",
        "base_url": "https://www.hamropatro.com",
        "fallback_url": "https://www.hamropatro.com/news",
        "allowed": True,
        "rate_limit_per_minute": 3,
        "default_language": "ne",
        "selector_config": config,
    }).parse(LISTING_HTML)

    assert adapter().parse(LISTING_HTML)[0]["title"] == (
        "इजरायलमा वामपन्थ र दक्षिणपन्थको अर्थ के हो ? विपक्षी को हो ?"
    )
    assert "onlinekhabar.com" in fallback[0]["title"]
    assert "मिनेट अघि" in fallback[0]["title"]
    assert "निर्वाचनले" in fallback[0]["title"]


# ---------------------------------------------------------------------------
# The link and the picture
# ---------------------------------------------------------------------------

def test_the_link_is_the_detail_url_and_is_absolute():
    entries = adapter().parse(LISTING_HTML)

    assert entries[0]["link"] == (
        "https://www.hamropatro.com/news/detail/"
        "4924822949913971-ijarayalama-vamapanth-r-dakshinapanthako-arth-ke-ho"
    )
    assert entries[1]["link"].startswith(
        "https://www.hamropatro.com/news/detail/4926307025576464-"
    )


def test_the_picture_is_the_storys_own():
    """
    The picture is the one in the card's own <img>, served from
    image.hamropatro.com. It is in `src`, so no lazy-load attribute
    is needed.
    """
    entries = adapter().parse(LISTING_HTML)

    with_picture = [e for e in entries if e["image"]]
    assert len(with_picture) == 1
    assert with_picture[0]["image"].startswith("https://image.hamropatro.com/imgs/")
    assert with_picture[0]["image"].endswith(".webp")

    # A card with no picture stores no picture rather than somebody
    # else's.
    without_picture = [e for e in entries if not e["image"]]
    assert len(without_picture) == 1
    assert without_picture[0]["link"].endswith(
        "banggaladeshaka-mohammad-saiphauddinale-pokharabat-enapiel-khelne"
    )


def test_the_section_links_are_not_stories():
    """
    /news/national, /news/sports and /news/economics are the section
    tabs in the page's own navigation. They are not cards and they do
    not become headlines.
    """
    entries = adapter().parse(LISTING_HTML)

    assert all("/news/detail/" in e["link"] for e in entries)
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# The migration and the tested configuration cannot drift apart
# ---------------------------------------------------------------------------

def test_the_migration_stores_the_selector_config_that_was_verified():
    """
    Reads the selector_config straight out of
    supabase/migrations/0030_hamropatro_news.sql, un-escapes it the
    way Postgres would ('' inside a string literal is one ') and
    parses it as JSON. If the migration is edited to a different
    selector, this test fails.
    """
    sql = MIGRATION.read_text(encoding="utf-8")

    literals = re.findall(r"'(\{\"item\".*?\})'::jsonb", sql, re.DOTALL)
    assert len(literals) == 2, "expected one row per section"

    for literal in literals:
        config = json.loads(literal.replace("''", "'"))
        assert config == SELECTOR_CONFIG


def test_the_migration_names_both_sections_and_is_idempotent():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "Hamro Patro News" in sql
    assert "Hamro Patro National" in sql
    assert "https://www.hamropatro.com/news/national" in sql
    assert "on conflict (name) do update set" in sql

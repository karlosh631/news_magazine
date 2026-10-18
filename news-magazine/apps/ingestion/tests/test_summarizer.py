"""
Tests for the four-point extractive summariser.

The contract that matters: every point is a verbatim sentence from the
article, and an article always yields up to four of them. Stage 1's
threshold collapsed a 35-sentence article to a single point; these tests
pin the behaviour that fixed.
"""

from __future__ import annotations

from app.services.summarizer import (
    NUM_POINTS,
    html_to_text,
    score_sentences,
    split_sentences,
    summarize_article,
    summarize_from_html,
)
from app.sources.media import extract_article_text


SAMPLE = """
<p>The Mid-Hill Highway passes through steep hillsides above Sindhuli
district, and traffic on the section is suspended every monsoon.</p>

<p>A Tata Sumo jeep carrying seven passengers fell into the Sunkoshi river
from the Nagthan bridge at around half past two in the morning.</p>

<p>Police said the vehicle, registration number Ko 1 Ja 2976, dropped about
two hundred metres before reaching the riverbed below.</p>

<p>Rescue teams from the Nepal Police and the Armed Police Federation were
deployed to the site within an hour of the accident being reported.</p>

<p>Three women were confirmed dead at the scene and four passengers were
rescued and taken to a hospital in the district.</p>

<p>The driver, a 29-year-old from Okhaldhunga, remained unconscious and was
admitted for observation.</p>

<p>Authorities have asked the highway office to inspect the guardrail at
the accident location before the monsoon ends.</p>

<p>Local residents said heavy rain through the night washed debris onto
the carriageway.</p>
"""


# =============================================================
# Text extraction
# =============================================================

def test_extract_article_text_finds_paragraphs():
    text = extract_article_text(SAMPLE)
    assert text is not None
    assert "Sunkoshi" in text
    assert "Rescue teams" in text


def test_extract_article_text_returns_none_for_thin_pages():
    assert extract_article_text("<html><body><p>Hi</p></body></html>") is None


def test_html_to_text_drops_script_blocks():
    flat = html_to_text("<script>var x = 'not content';</script><p>Real sentence here.</p>")
    assert "not content" not in flat
    assert "Real sentence" in flat


# =============================================================
# Sentence splitting
# =============================================================

def test_split_sentences_handles_devanagari_danda():
    text = (
        "बडादसैँको आठौँ दिन महाअष्टमीका अवसरमा मैतीदेवी मन्दिरमा भक्तजनको घुइँचो बढ्यो। "
        "मन्दिर व्यवस्थापन समितिले दर्शनार्थीको व्यवस्थापन गरेको थियो। "
        "बलिदानी परिपाटी यस वर्ष पनि अपरिवर्तन भएको छैन।"
    )
    sentences = split_sentences(text)
    assert len(sentences) == 3


def test_split_sentences_handles_latin_periods():
    text = (
        "The jeep fell two hundred metres into the river. "
        "Police confirmed three deaths at the scene. "
        "Rescue teams reached the site within an hour."
    )
    assert len(split_sentences(text)) == 3


def test_split_sentences_drops_boilerplate():
    text = "Read more about this story. A genuine sentence with plenty of words in it."
    sentences = split_sentences(text)
    assert not any(s.lower().startswith("read more") for s in sentences)


def test_split_sentences_drops_too_short_fragments():
    assert split_sentences("Yes.") == []


# =============================================================
# Scoring
# =============================================================

def test_score_sentences_prefers_the_lede():
    sentences = split_sentences(SAMPLE)
    scores = score_sentences(sentences)
    assert len(scores) == len(sentences)
    assert scores[0] > 0


def test_score_sentences_handles_empty_input():
    assert score_sentences([]) == []


# =============================================================
# The four-point contract
# =============================================================

def test_summarize_returns_exactly_four_points_for_a_full_article():
    summary = summarize_article(SAMPLE)
    assert len(summary.points) == NUM_POINTS == 4


def test_summary_points_are_verbatim_sentences_from_the_article():
    """
    The core guarantee. Nothing is generated, so every point must appear
    in the source text (modulo whitespace).
    """
    summary = summarize_article(SAMPLE)
    flat = " ".join(html_to_text(SAMPLE).split())

    for point in summary.points:
        assert " ".join(point.split()) in flat, f"not verbatim: {point!r}"


def test_summary_returns_fewer_points_when_the_article_is_short():
    """Two distinct sentences cannot become four points."""
    short = (
        "<p>The council approved the budget on Tuesday evening after a long "
        "session that ran past midnight.</p>"
        "<p>Three members abstained from the vote, citing funding concerns for "
        "the proposed highway project.</p>"
    )
    summary = summarize_article(extract_article_text(short))
    assert 0 < len(summary.points) < NUM_POINTS
    assert len(set(summary.points)) == len(summary.points)


def test_summary_of_nothing_is_empty_not_an_error():
    summary = summarize_article("")
    assert summary.points == []


def test_summary_is_deterministic():
    first = summarize_article(SAMPLE)
    second = summarize_article(SAMPLE)
    assert first.points == second.points


def test_summary_points_do_not_repeat_each_other():
    summary = summarize_article(SAMPLE)
    assert len(set(summary.points)) == len(summary.points)


def test_summary_reports_how_many_sentences_it_saw():
    summary = summarize_article(SAMPLE)
    assert summary.source_sentences > len(summary.points)


# =============================================================
# summarize_from_html fallbacks
# =============================================================

def test_from_html_prefers_the_body():
    summary = summarize_from_html(extract_article_text(SAMPLE), headline="Jeep falls into river")
    assert summary.method == "extractive"
    assert len(summary.points) == 4


def test_from_html_falls_back_to_excerpt_when_body_is_missing():
    """Kathmandu Post serves its body client-side; only meta survives."""
    summary = summarize_from_html(
        None,
        headline="Three women killed",
        fallback_excerpt=(
            "A Tata Sumo jeep plunged into the Sunkoshi river early Sunday. "
            "Police said the vehicle fell about two hundred metres. "
            "Three bodies were recovered at the accident site. "
            "Four passengers were rescued and taken to hospital."
        ),
    )
    assert summary.points
    assert summary.method in {"extractive_from_excerpt", "excerpt_fallback"}


def test_from_html_returns_empty_when_there_is_nothing_at_all():
    summary = summarize_from_html(None, headline="Nothing", fallback_excerpt=None)
    assert summary.points == []


def test_from_html_fallback_never_invents_text():
    """
    Even the fallback path must only surface supplied text. A summary
    for a bodiless article comes from the excerpt or nowhere.
    """
    excerpt = "The council approved the budget on Tuesday evening."
    summary = summarize_from_html(None, headline="Budget", fallback_excerpt=excerpt)
    for point in summary.points:
        assert point.strip("…") in excerpt or excerpt[:40] in point

# =============================================================
# Excerpt / points overlap
# =============================================================

def test_excerpt_drops_sentences_the_points_already_say():
    """
    Both are extracted from the same article, so storing them as-is
    renders the same paragraph twice on the article page.
    """
    from app.services.summarizer import excerpt_without_points

    points = [
        "More than two years after the Nepali nurse vanished in Virginia, "
        "her husband is on trial for murder, though her body has never been found.",
        "Here are 14 key questions about the case and the evidence.",
    ]
    excerpt = (
        "More than two years after the Nepali nurse vanished in Virginia, "
        "her husband is on trial for murder, though her body has never been "
        "found. He has pleaded not guilty. Here are 14 key questions about "
        "the case and the evidence."
    )

    result = excerpt_without_points(excerpt, points)

    assert result == "He has pleaded not guilty."


def test_excerpt_is_dropped_when_nothing_distinct_survives():
    from app.services.summarizer import excerpt_without_points

    points = ["Nepal imports two million packs of playing cards in two months."]
    assert excerpt_without_points(points[0], points) is None


def test_excerpt_is_kept_verbatim_when_there_are_no_points():
    from app.services.summarizer import excerpt_without_points

    text = "A sentence with no summary to overlap."
    assert excerpt_without_points(text, []) == text
    assert excerpt_without_points(text, None) == text


def test_excerpt_overlap_works_on_devanagari():
    from app.services.summarizer import excerpt_without_points

    points = ["प्रधानमन्त्री श्री बलेन्द्र शाहले आज संसदमा भाषण दिनुभयो।"]
    excerpt = (
        "प्रधानमन्त्री श्री बलेन्द्र शाहले आज संसदमा भाषण दिनुभयो। "
        "उहाँले देशको आर्थिक अवस्था बारे मन्त्रालयलाई निर्देशन दिनुभयो।"
    )

    assert excerpt_without_points(excerpt, points) == (
        "उहाँले देशको आर्थिक अवस्था बारे मन्त्रालयलाई निर्देशन दिनुभयो।"
    )


def test_excerpt_splitter_keeps_short_sentences():
    """
    It must not reuse split_sentences(), which drops anything under
    MIN_SENTENCE_WORDS. That filter is right for choosing four
    substantial points and wrong for asking which sentences the points
    already cover — "He has pleaded not guilty." is exactly the detail
    worth keeping.
    """
    from app.services.summarizer import excerpt_without_points, split_sentences

    excerpt = (
        "Nepal's parliament debated the budget for four hours on Tuesday. "
        "It passed. The prime minister had earlier warned of a shortfall."
    )
    assert len(split_sentences(excerpt)) < 3  # the short one is filtered out

    result = excerpt_without_points(excerpt, ["It passed."])
    assert result is not None
    assert "It passed." not in result

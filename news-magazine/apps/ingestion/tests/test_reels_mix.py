"""
Tests for the reels feed's shape: the language mix, the diversity, and
the rate limit.

Three things about this file are worth stating up front, because each
of them was a live bug and each of them looked like something else:

  1. The seventy-thirty mix. The reader asked for about seventy per
     cent of the reels from the wider Hindi and international
     entertainment pool and about thirty from the Nepali one. The
     split was made by a marker in the term list - `ne:` - and the
     marker was being STRIPPED by the term cleaner before the code
     that groups the terms ever looked for it. Every term landed in
     the "other" group and the Nepali group was empty, always, and
     the feed was a hundred per cent "other" no matter what the
     operator put in the list.

  2. The rate limit. Wikimedia answers a burst with HTTP 429 and a
     `Retry-After` header. The reply was thrown away and reported as
     "upstream returned a body that is not JSON", which is true and
     useless: it named a decoding failure instead of the throttle
     that had actually happened. Twenty-one searches went through and
     the twenty-second was throttled, so every term after it - all of
     them in the second half of the list, which is where the Nepali
     terms are - failed. The Nepali group was not missing because
     Commons had no Nepali material. It was missing because the pass
     was throttled halfway through.

  3. The duplicates. "Reels mostly are duplicates." The files were
     different files, so the store's deduplication never saw them as
     the same, but the screen said the same thing nine times: nine
     clips from one series, found by one term. The fix is at the
     source - a term may not fill the feed by itself.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.reels import providers as reels_providers
from app.services.reels.keys import build_pool
from app.services.reels.providers import (
    COMMONS_MAX_CLIPS_PER_TERM,
    WikimediaSource,
    apply_language_mix,
    build_source,
)
from app.services.reels.types import Reel


def run(coro):
    return asyncio.run(coro)


def make_reel(external_id: str, group: str | None = None) -> Reel:
    return Reel(
        source_key="wikimedia",
        external_id=external_id,
        video_url=f"https://upload.wikimedia.org/{external_id}.webm",
        title=external_id,
        language_group=group,
    )


class FakeResponse:
    """
    A response, and the body a real one would carry.

    A 429 from Wikimedia is a plain-text notice - "You are making too
    many requests to the API" - and not JSON at all. A fake that
    answered with `{}` would be a lie that hides the very bug these
    tests are about: the reply was reported as a DECODING failure
    because the body was not JSON, and that is what the throttle
    looked like from the inside.
    """

    def __init__(self, payload=None, status_code=200, headers=None, text=None):
        self._payload = payload if payload is not None else {"query": {"pages": []}}
        self.status_code = status_code
        self.headers = headers or {}
        self._text = text
        self.request = SimpleNamespace(
            url=SimpleNamespace(host="commons.wikimedia.org")
        )

    def json(self):
        if self._text is not None:
            raise ValueError("not JSON")
        return self._payload


class ScriptedClient:
    """Returns each scripted response in turn, and records every call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None):
        self.calls.append({"params": dict(params or {}), "headers": headers or {}})
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def wikimedia(hint: str, client) -> WikimediaSource:
    return build_source(
        "wikimedia",
        pool=build_pool("wikimedia", None),
        client=client,
        hint=hint,
    )


# =============================================================
# THE MARKER
# =============================================================


def test_the_nepali_marker_survives_the_term_cleaner():
    """
    The regression. The cleaner stripped `ne:` and the grouping looked
    for it afterwards, so the Nepali group was empty for ever.
    """
    source = wikimedia("folk song,ne:tharu folk song,sangeet", client=None)

    other, nepali = source._grouped_terms()

    assert nepali == ["tharu folk song"]
    assert other == ["folk song", "sangeet"]


def test_the_search_terms_carry_no_marker():
    """
    The marker is a marker and not part of the term: the search
    expression must not carry it, or the search matches nothing.
    """
    source = wikimedia("folk song,ne:tharu folk song", client=None)

    assert source._search_terms() == ["folk song", "tharu folk song"]


def test_the_two_readers_of_the_term_list_agree():
    """
    One parser, two readers. They can never disagree about which
    terms exist or which of them are Nepali.
    """
    source = wikimedia("alpha,ne:bravo,charlie,ne:delta", client=None)

    other, nepali = source._grouped_terms()
    everything = source._search_terms()

    assert sorted(everything) == sorted(other + nepali)
    assert len(other) == 2
    assert len(nepali) == 2


def test_a_term_may_be_in_both_groups():
    """
    The dedup key carries the flag, so the same word can be searched
    once for each group if the operator asks for it.
    """
    source = wikimedia("song,ne:song", client=None)

    other, nepali = source._grouped_terms()

    assert other == ["song"]
    assert nepali == ["song"]


# =============================================================
# THE MIX
# =============================================================


def test_the_mix_is_seventy_thirty():
    """
    The reader's own numbers, as a test.
    """
    other = [make_reel(f"other-{i}") for i in range(30)]
    nepali = [make_reel(f"nepali-{i}") for i in range(30)]

    mixed = apply_language_mix(other, nepali, limit=30)

    assert len(mixed) == 30
    assert len([r for r in mixed if r.external_id.startswith("other")]) == 21
    assert len([r for r in mixed if r.external_id.startswith("nepali")]) == 9


def test_a_short_nepali_group_gives_its_place_to_the_other_one():
    """
    A feed that is empty because Commons had no Nepali material this
    week is not better than a feed that is all Nepali.
    """
    other = [make_reel(f"other-{i}") for i in range(30)]
    nepali = [make_reel(f"nepali-{i}") for i in range(2)]

    mixed = apply_language_mix(other, nepali, limit=30)

    assert len(mixed) == 30
    assert len([r for r in mixed if r.external_id.startswith("nepali")]) == 2
    assert len([r for r in mixed if r.external_id.startswith("other")]) == 28


def test_a_short_other_group_gives_its_place_to_the_nepali_one():
    """
    The same rule the other way round, so the mix cannot be gamed by
    emptying one group.
    """
    other = [make_reel(f"other-{i}") for i in range(3)]
    nepali = [make_reel(f"nepali-{i}") for i in range(30)]

    mixed = apply_language_mix(other, nepali, limit=30)

    assert len(mixed) == 30
    assert len([r for r in mixed if r.external_id.startswith("nepali")]) == 27


def test_the_mix_never_exceeds_the_limit():
    other = [make_reel(f"other-{i}") for i in range(50)]
    nepali = [make_reel(f"nepali-{i}") for i in range(50)]

    assert len(apply_language_mix(other, nepali, limit=12)) == 12
    assert len(apply_language_mix(other, nepali, limit=0)) == 0


def test_an_empty_group_leaves_the_other_one_alone():
    other = [make_reel(f"other-{i}") for i in range(5)]

    mixed = apply_language_mix(other, [], limit=5)

    assert len(mixed) == 5


# =============================================================
# THE DIVERSITY
# =============================================================


def test_one_term_may_not_fill_the_feed(monkeypatch):
    """
    "Reels mostly are duplicates." Nine clips of one series is one
    clip nine times, however many different files they are.

    The fake Commons below answers EVERY term with the same eight
    pages, so without a cap the whole feed comes from one term.
    """
    monkeypatch.setattr(reels_providers, "SEARCH_PACING_SECONDS", 0.0)

    page = {
        "title": "File:Clip.webm",
        "videoinfo": [
            {
                "url": "https://upload.wikimedia.org/Clip.webm",
                "width": 1080,
                "height": 1920,
                "mime": "video/webm",
                "duration": 12.0,
                "derivatives": [
                    {
                        "transcodekey": "1080p.vp9.webm",
                        "type": 'video/webm; codecs="vp9, opus"',
                        "src": "https://upload.wikimedia.org/Clip.1080p.vp9.webm",
                        "width": 1080,
                        "height": 1920,
                    }
                ],
                "extmetadata": {
                    "LicenseShortName": {"value": "CC0"},
                    "Artist": {"value": "Someone"},
                    "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/zero/1.0/"},
                    # The description is what carries the Nepal signal,
                    # and the subject filter reads the title AND the
                    # description. A clip with neither is dropped as
                    # off-topic, which is what the provider is for.
                    "ImageDescription": {
                        "value": "A folk song recorded in Nepal."
                    },
                },
            }
        ],
        "imageinfo": [
            {
                "url": "https://upload.wikimedia.org/Clip.webm",
                "thumburl": "https://upload.wikimedia.org/Clip.webm/1280px-Clip.webm.jpg",
                "width": 1920,
                "height": 1080,
                "mime": "video/webm",
            }
        ],
    }

    def pages_for(term: str) -> list[dict]:
        """
        Each term gets its OWN files, so the title deduplication does
        not empty every term but the first and the cap is what is
        actually under test.
        """
        out = []
        for i in range(8):
            copy = dict(page)
            copy["title"] = f"File:{term} clip {i}.webm"
            out.append(copy)
        return out

    class PerTermClient:
        async def get(self, url, *, params=None, headers=None):
            expression = str((params or {}).get("gsrsearch") or "")
            term = expression.split("intitle:")[-1].strip('"')
            return FakeResponse({"query": {"pages": pages_for(term)}})

    source = wikimedia("folk song,song,dance,ne:tharu folk song", client=PerTermClient())

    reels = run(source.fetch(limit=30))

    # Four terms, three clips each: without the cap the feed would be
    # thirty-two clips from four terms and four of them would be the
    # same eight files over and over.
    assert len(reels) == COMMONS_MAX_CLIPS_PER_TERM * 4, len(reels)

    # And no term may have contributed more than the cap.
    from collections import Counter

    per_term = Counter(reel.title.split(" clip ")[0] for reel in reels)
    assert max(per_term.values()) <= COMMONS_MAX_CLIPS_PER_TERM, per_term


def test_the_cap_is_three():
    """
    Three is enough for a term to be properly represented and few
    enough that a feed of thirty draws on at least seven of them.
    """
    assert COMMONS_MAX_CLIPS_PER_TERM == 3


# =============================================================
# THE SUBJECT FILTER
# =============================================================


def test_a_clip_with_no_nepal_signal_is_dropped(monkeypatch):
    """
    The filter that keeps the feed about Nepal, and that the wider
    entertainment pool has to live inside.

    It reads the title AND the description, and either one answering
    is enough - so the test drops a clip with neither, and keeps a
    clip whose description mentions Nepal.
    """
    monkeypatch.setattr(reels_providers, "SEARCH_PACING_SECONDS", 0.0)

    def page_for(title: str, description: str) -> dict:
        return {
            "title": f"File:{title}",
            "videoinfo": [
                {
                    "url": "https://upload.wikimedia.org/Clip.webm",
                    "width": 1080, "height": 1920, "mime": "video/webm",
                    "duration": 12.0,
                    "derivatives": [
                        {
                            "transcodekey": "1080p.vp9.webm",
                            "type": 'video/webm; codecs="vp9, opus"',
                            "src": "https://upload.wikimedia.org/Clip.1080p.vp9.webm",
                            "width": 1080, "height": 1920,
                        }
                    ],
                    "extmetadata": {
                        "LicenseShortName": {"value": "CC0"},
                        "Artist": {"value": "Someone"},
                        "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/zero/1.0/"},
                        "ImageDescription": {"value": description},
                    },
                }
            ],
            "imageinfo": [
                {
                    "url": "https://upload.wikimedia.org/Clip.webm",
                    "thumburl": "https://upload.wikimedia.org/Clip.webm/1280px-Clip.webm.jpg",
                    "width": 1920, "height": 1080, "mime": "video/webm",
                }
            ],
        }

    pages = [
        page_for("Off topic clip.webm", "A timelapse of a rocket."),
        page_for("A folk song in Nepal.webm", "Recorded in the Terai."),
    ]

    class OneShot:
        async def get(self, url, *, params=None, headers=None):
            return FakeResponse({"query": {"pages": pages}})

    source = wikimedia("folk song", client=OneShot())
    reels = run(source.fetch(limit=10))

    titles = [reel.title for reel in reels]
    assert titles == ["A folk song in Nepal.webm"], titles


# =============================================================
# THE RATE LIMIT
# =============================================================


def test_a_throttle_is_waited_out_and_the_request_is_made_again(monkeypatch):
    """
    The regression. Wikimedia answers a burst with HTTP 429 and a
    `Retry-After` header, and the reply used to be reported as "not
    JSON" - a decoding failure where the truth was a throttle.
    """
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(reels_providers.asyncio, "sleep", fake_sleep)

    client = ScriptedClient(
        [
            FakeResponse(
                status_code=429,
                headers={"retry-after": "2"},
                text="You are making too many requests to the API.",
            ),
            FakeResponse({"query": {"pages": []}}, status_code=200),
        ]
    )
    source = wikimedia("", client=client)

    payload = run(source._get_json({"action": "query"}))

    assert payload == {"query": {"pages": []}}
    assert len(client.calls) == 2
    assert slept == [2.0]


def test_a_throttle_without_a_retry_after_still_waits(monkeypatch):
    """
    The header is the only instruction in the reply, but a reply
    without one is still a throttle.
    """
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(reels_providers.asyncio, "sleep", fake_sleep)

    client = ScriptedClient(
        [
            FakeResponse(
                status_code=429, text="You are making too many requests to the API."
            ),
            FakeResponse({"query": {"pages": []}}, status_code=200),
        ]
    )
    source = wikimedia("", client=client)

    run(source._get_json({"action": "query"}))

    assert len(client.calls) == 2
    assert slept, "a throttle with no Retry-After must still be waited out"


def test_a_throttle_that_never_lets_up_is_reported(monkeypatch):
    """
    The cap on the waiting. A provider that asks for an hour is not
    one this pass can serve, and the pass has a timeout of its own.
    """
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(reels_providers.asyncio, "sleep", fake_sleep)

    client = ScriptedClient(
        [
            FakeResponse(
                status_code=429, text="You are making too many requests to the API."
            )
        ]
    )
    source = wikimedia("", client=client)

    with pytest.raises(reels_providers.ProviderError):
        run(source._get_json({"action": "query"}))

    assert len(client.calls) == reels_providers.RATE_LIMIT_RETRIES + 1


def test_the_wait_is_capped(monkeypatch):
    """
    A `Retry-After: 43` is a bucket drained by something else on the
    same address. The cap is what stops the pass sleeping through it.
    """
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(reels_providers.asyncio, "sleep", fake_sleep)

    client = ScriptedClient(
        [
            FakeResponse(
                status_code=429,
                headers={"retry-after": "43"},
                text="You are making too many requests to the API.",
            ),
            FakeResponse({"query": {"pages": []}}, status_code=200),
        ]
    )
    source = wikimedia("", client=client)

    run(source._get_json({"action": "query"}))

    assert slept == [reels_providers.RATE_LIMIT_MAX_WAIT_SECONDS]


# =============================================================
# THE COVERAGE
# =============================================================


def test_every_term_is_asked_for_before_any_term_is_asked_twice(monkeypatch):
    """
    The other half of the throttle fix, and the reason the Nepali
    group was empty for so long.

    The budget used to be spent on the FIRST terms of the list: a
    hundred requests and four pages a term reached ten terms and no
    more, so the other twenty-four were never searched at all. The
    Nepali terms are in the second half of the list, so they were the
    ones that never ran.
    """
    monkeypatch.setattr(reels_providers, "SEARCH_PACING_SECONDS", 0.0)

    client = ScriptedClient([FakeResponse({"query": {"pages": []}})])
    source = wikimedia("alpha,bravo,charlie,delta,echo,foxtrot", client=client)

    run(source.fetch(limit=10))

    searched = [
        call["params"]["gsrsearch"]
        for call in client.calls
        if "gsroffset" not in call["params"]
    ]

    assert len(searched) == 6, searched
    assert len(set(searched)) == 6, searched


def test_the_wikimedia_requests_carry_the_api_user_agent(monkeypatch):
    """
    `Api-User-Agent` is the header Wikimedia's rate limiter reads.
    A request with only the ordinary header is counted as an
    unidentified client, and unidentified clients get the strictest
    limit.
    """
    monkeypatch.setattr(reels_providers, "SEARCH_PACING_SECONDS", 0.0)

    client = ScriptedClient([FakeResponse({"query": {"pages": []}})])
    source = wikimedia("folk song", client=client)

    run(source.fetch(limit=5))

    assert client.calls
    headers = client.calls[0]["headers"]
    assert headers.get("Api-User-Agent"), headers
    assert "new24hr.vercel.app" in headers["Api-User-Agent"]

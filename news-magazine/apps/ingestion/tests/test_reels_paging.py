"""
The reels pool has to actually GROW, and the feed has to actually MOVE.

THE BUG THIS FILE EXISTS FOR
----------------------------
The Reels tab stopped updating. The worker logged a healthy run, the
provider said it had stored clips, and the page showed the same twelve
clips it had shown for days.

The cause was in the search, not the storage. Commons was asked for
the NEWEST uploads first (`gsrsort=create_timestamp_desc`) and the
provider asked for exactly ONE page of them. So every pass asked for
the same twenty-five newest files for every configured term, got the
same twenty-five files back, found them already in the database, and
stored nothing. The pool never grew past the newest slice of each
term, and the feed never had anything new to show.

The fix is to walk further down each term's results - and to ROTATE
where the walk starts, so a pass covers the whole catalogue over the
course of a day without any single pass having to walk all of it.

These tests pin that behaviour. They are about the requests the
provider MAKES, not about the reels it returns, because the requests
are the thing that was wrong.
"""

from __future__ import annotations

import pytest

from app.services.reels.providers import (
    COMMONS_MAX_PAGE_SIZE,
    COMMONS_MAX_REQUESTS_PER_PASS,
    COMMONS_PAGES_PER_TERM,
    COMMONS_ROTATION_BANDS,
    build_source,
)
from app.services.reels.keys import build_pool


def _commons(hint: str = "nepal,kathmandu"):
    return build_source(
        "wikimedia", pool=build_pool("wikimedia", None), client=None, hint=hint
    )


def _page(titles: list[str]) -> dict:
    """A Commons search page holding `titles`, with videoinfo on each."""
    return {
        "query": {
            "pages": [
                {
                    "pageid": index + 1,
                    "title": f"File:{title}.webm",
                    "imageinfo": [
                        {
                            "url": (
                                "https://upload.wikimedia.org/wikipedia/commons/"
                                f"a/ab/{title}.webm"
                            ),
                            "thumburl": (
                                "https://upload.wikimedia.org/wikipedia/commons/thumb/"
                                f"a/ab/{title}.webm/640px-{title}.webm.jpg"
                            ),
                            "mime": "video/webm",
                            "mediatype": "VIDEO",
                            "width": 1920,
                            "height": 1080,
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "License": {"value": "cc-by-sa-4.0"},
                                "LicenseUrl": {
                                    "value": "https://creativecommons.org/licenses/by-sa/4.0/"
                                },
                                "Artist": {"value": "A Photographer"},
                                "ImageDescription": {"value": "A street in Kathmandu."},
                            },
                        }
                    ],
                    "videoinfo": [
                        {
                            "duration": 12.5,
                            "width": 1920,
                            "height": 1080,
                            "mime": "video/webm",
                            "mediatype": "VIDEO",
                            "url": (
                                "https://upload.wikimedia.org/wikipedia/commons/"
                                f"a/ab/{title}.webm"
                            ),
                            "derivatives": [
                                {
                                    "src": (
                                        "https://upload.wikimedia.org/wikipedia/commons/"
                                        f"a/ab/{title}.webm"
                                    ),
                                    "type": 'video/webm; codecs="av01.0.08M.08"',
                                    "width": 1920,
                                    "height": 1080,
                                    "bandwidth": 528690,
                                }
                            ],
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "License": {"value": "cc-by-sa-4.0"},
                                "LicenseUrl": {
                                    "value": "https://creativecommons.org/licenses/by-sa/4.0/"
                                },
                                "Artist": {"value": "A Photographer"},
                            },
                        }
                    ],
                }
                for index, title in enumerate(titles)
            ]
        }
    }


def _full_page(index: int) -> dict:
    """A page that is completely full, so the walk does not stop early."""
    return _page([f"clip-{index}-{n}" for n in range(COMMONS_MAX_PAGE_SIZE)])


class _Recorder:
    """Stands in for the search, and remembers what it was asked for."""

    def __init__(self, pages=None):
        self.calls: list[dict] = []
        self._pages = pages

    async def __call__(self, params, term):
        self.calls.append({"params": dict(params), "term": term})
        if self._pages is not None:
            return self._pages
        offset = int(params.get("gsroffset") or 0)
        return _full_page(offset // COMMONS_MAX_PAGE_SIZE)

    @property
    def offsets(self) -> list[int]:
        return [int(call["params"].get("gsroffset") or 0) for call in self.calls]


# -------------------------------------------------------------------
# The walk
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_term_is_asked_for_more_than_one_page(monkeypatch):
    """
    The whole fix in one assertion: one page per term is what froze
    the feed, so a term must now be walked.
    """
    source = _commons("nepal")
    recorder = _Recorder()
    monkeypatch.setattr(source, "_search_with_retry", recorder)

    await source._fetch(30)

    assert len(recorder.calls) > 1, recorder.calls
    assert len(recorder.calls) == COMMONS_PAGES_PER_TERM, recorder.calls


@pytest.mark.asyncio
async def test_the_newest_page_is_always_asked_for(monkeypatch):
    """
    A new upload appears at the TOP of a newest-first search. A pass
    that skipped the first page would never notice one, so the walk
    always starts there.
    """
    source = _commons("nepal")
    recorder = _Recorder()
    monkeypatch.setattr(source, "_search_with_retry", recorder)

    await source._fetch(30)

    assert recorder.offsets[0] == 0, recorder.offsets


@pytest.mark.asyncio
async def test_the_deep_pages_are_further_down_the_results(monkeypatch):
    """
    The walk has to leave the first page, or it is the same one page
    again.
    """
    source = _commons("nepal")
    recorder = _Recorder()
    monkeypatch.setattr(source, "_search_with_retry", recorder)

    await source._fetch(30)

    assert max(recorder.offsets) >= COMMONS_MAX_PAGE_SIZE, recorder.offsets


@pytest.mark.asyncio
async def test_a_short_page_ends_the_walk_for_that_term(monkeypatch):
    """
    A term with four results has four results. Asking for the page
    after them is asking for nothing, so the walk stops.
    """
    source = _commons("nepal")
    recorder = _Recorder(pages=_page(["only", "two", "of", "them"]))
    monkeypatch.setattr(source, "_search_with_retry", recorder)

    await source._fetch(30)

    assert len(recorder.calls) == 1, recorder.calls


@pytest.mark.asyncio
async def test_the_walk_is_bounded_by_the_request_budget(monkeypatch):
    """
    A misconfigured list of 200 terms must not turn one pass into 200
    requests against a free API.
    """
    source = _commons(",".join(f"term{i}" for i in range(200)))
    recorder = _Recorder()
    monkeypatch.setattr(source, "_search_with_retry", recorder)

    await source._fetch(30)

    assert len(recorder.calls) <= COMMONS_MAX_REQUESTS_PER_PASS, len(recorder.calls)


@pytest.mark.asyncio
async def test_a_term_that_fails_does_not_cost_the_terms_that_worked(monkeypatch):
    """
    Commons reports saturation as an error inside a successful HTTP
    200. Before this, one busy term in twelve took the whole pass with
    it, and the Reels tab went quiet while the worker logged a healthy
    run.
    """
    from app.services.reels.providers import ProviderError

    source = _commons("nepal,kathmandu")
    calls: list[str] = []

    async def flaky(params, term):
        calls.append(term)
        if term == "nepal":
            raise ProviderError("cirrussearch-too-busy-error: try again later")
        return _full_page(0)

    monkeypatch.setattr(source, "_search_with_retry", flaky)

    reels = await source._fetch(30)

    assert "kathmandu" in calls, calls
    assert reels, "the working term's clips must still be returned"


# -------------------------------------------------------------------
# The rotation
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_band_rotates_with_the_hour(monkeypatch):
    """
    The deep pages ROTATE. If every pass walked the same band, the
    rest of the catalogue would never be reached - the pool would be
    bigger than it was, and still frozen.
    """
    from app.services.reels import providers as providers_module

    source = _commons("nepal")

    def _offsets_at(hour: int) -> list[int]:
        class _FixedDatetime:
            @staticmethod
            def now(tz=None):
                import datetime as _dt

                return _dt.datetime(2026, 10, 22, hour, 0, 0, tzinfo=_dt.timezone.utc)

        monkeypatch.setattr(providers_module, "datetime", _FixedDatetime)
        recorder = _Recorder()
        monkeypatch.setattr(source, "_search_with_retry", recorder)
        import asyncio

        asyncio.get_event_loop().run_until_complete(source._fetch(30))
        return recorder.offsets

    early = _offsets_at(0)
    later = _offsets_at(1)

    assert early != later, (early, later)
    # And the first page is the same either way, because that is where
    # a new upload appears.
    assert early[0] == 0 and later[0] == 0, (early, later)


def test_the_rotation_bands_cover_the_window():
    """
    The bands have to tile the results without gaps, or a page would
    never be asked for.
    """
    assert COMMONS_ROTATION_BANDS > 0
    assert COMMONS_PAGES_PER_TERM >= 2
    # The window is a strict subset of the bands, so consecutive bands
    # overlap rather than leaving a hole.
    assert COMMONS_PAGES_PER_TERM - 1 < COMMONS_ROTATION_BANDS

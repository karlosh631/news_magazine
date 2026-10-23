"""
The artwork backlog sweep must not stop at the first row it cannot fix.

THE RUN THAT DID STOP
---------------------
The first dispatch of "Refresh images" walked five rows, confirmed them,
and then died:

    KeyError: 'no live picture on the page'

The count of WHY a row could not be proven was kept in a plain dict, and
the counting line was `reasons["no live picture on the page"] += 1` -
a subscript on a key that had never been set. The first row whose page
offered no picture of its own raised, the script died, the workflow
step failed, and the SECOND pass - the one that finds the pictures that
belong to another story - was skipped, because a step that fails stops
the steps after it.

So the row that was merely unprovable took down the run, and with it
the run that would have fixed the rows that were actually wrong.

These specs pin the two halves of the repair:

  - a row that cannot be proven is COUNTED, and the count is PRINTED
    (a run that says "149 could not be proven" and nothing else has to
    be repeated with more printing; a run that says which outlet and
    why is a diagnosis);
  - the run ends with the pass that checks the suspect pictures, no
    matter how the first pass went.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import refresh_images


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeMediaClient:
    """Stands in for `media_client()`, an async context manager."""

    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


def _row(**over):
    row = dict(
        id="row-1",
        headline="A story with a picture",
        slug="a-story",
        source_article_url="https://kathmandupost.com/news/a-story",
        canonical_url=None,
        featured_image_url="https://cdn.test/stored.jpg",
        image_highres_url="https://cdn.test/stored.jpg",
        image_verified_at=None,
        source_name_snapshot="Kathmandu Post",
        published_at="2026-10-23T06:00:00+00:00",
    )
    row.update(over)
    return row


def _page(*, empty: bool = False):
    return SimpleNamespace(
        is_empty=lambda: empty,
        source_url="https://kathmandupost.com/news/a-story",
        declared_images=[],
    )


@pytest.fixture
def sweep(monkeypatch):
    """
    A sweep with the world replaced by fakes.

    `live` is what the probe answers for every row; `rows` is what the
    database hands back.
    """
    state = SimpleNamespace(live=None, rows=[_row()], empty=False)

    async def fake_db_call(_fn):
        return _FakeResponse(state.rows)

    async def fake_extract(url, client):
        return _page(empty=state.empty)

    async def fake_resolve(candidates, validator, **kwargs):
        return state.live

    monkeypatch.setattr(refresh_images, "get_settings", lambda: SimpleNamespace(
        image_validation_timeout_seconds=1.0
    ))
    monkeypatch.setattr(refresh_images, "get_db", lambda: object())
    monkeypatch.setattr(refresh_images, "db_call", fake_db_call)
    monkeypatch.setattr(refresh_images, "media_client", _FakeMediaClient)
    monkeypatch.setattr(refresh_images, "extract_from_article", fake_extract)
    monkeypatch.setattr(refresh_images, "candidate_images", lambda *a, **k: [])
    monkeypatch.setattr(refresh_images, "resolve_live_image", fake_resolve)
    return state


async def test_a_page_with_no_picture_is_counted_and_the_run_continues(
    sweep, capsys
):
    """
    The row that killed the first run: its page offers no picture of
    its own. The run must finish, and must say why.
    """
    sweep.live = None

    exit_code = await refresh_images.run(limit=10, delay=0, dry_run=True)

    printed = capsys.readouterr().out

    assert exit_code == 0
    assert "no live picture on the page" in printed
    # The count is printed, not merely kept: a run that says nothing
    # about WHY has to be repeated with more printing.
    assert "Why each row could not be proven:" in printed
    # And WHICH outlet it was, so the operator knows which switch to
    # turn.
    assert "Kathmandu Post" in printed


async def test_an_empty_page_is_counted_too(sweep, capsys):
    sweep.empty = True

    exit_code = await refresh_images.run(limit=10, delay=0, dry_run=True)

    printed = capsys.readouterr().out

    assert exit_code == 0
    assert "empty page" in printed


async def test_a_row_with_no_address_is_skipped_not_counted(sweep, capsys):
    sweep.rows = [_row(source_article_url=None, canonical_url=None)]

    exit_code = await refresh_images.run(limit=10, delay=0, dry_run=True)

    printed = capsys.readouterr().out

    assert exit_code == 0
    assert "1 skipped" in printed


async def test_a_provable_picture_is_reported_as_confirmed(sweep, capsys):
    sweep.live = "https://cdn.test/proven.jpg"

    exit_code = await refresh_images.run(limit=10, delay=0, dry_run=True)

    printed = capsys.readouterr().out

    assert exit_code == 0
    assert "1 confirmed" in printed
    assert "no live picture on the page" not in printed


async def test_a_failing_row_does_not_stop_the_rows_after_it(sweep, capsys):
    """
    One row that raises is one row that could not be proven. The rows
    after it still get their chance.
    """
    sweep.rows = [_row(id="row-1"), _row(id="row-2"), _row(id="row-3")]
    sweep.live = "https://cdn.test/proven.jpg"

    calls = {"n": 0}

    async def flaky_extract(url, client):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the publisher closed the connection")
        return _page()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(refresh_images, "extract_from_article", flaky_extract)
        exit_code = await refresh_images.run(limit=10, delay=0, dry_run=True)

    printed = capsys.readouterr().out

    assert exit_code == 0
    assert "RuntimeError" in printed
    assert "2 confirmed" in printed

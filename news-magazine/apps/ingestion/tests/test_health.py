"""
Which code is the deployment running?

"Is the worker running the current code?" is the question that matters
when a poll fails with an error from a line that no longer exists in the
tree. `KeyError: 'date'` was the symptom of a worker that had not been
redeployed since the code changed, and it was expensive to diagnose
because nothing said which code was running.

The answer is now on the health endpoint, which is the one URL every
monitor already reads.
"""

from __future__ import annotations

from app.main import deployed_branch, deployed_commit

COMMIT_VARIABLES = (
    "RENDER_GIT_COMMIT",
    "VERCEL_GIT_COMMIT_SHA",
    "GIT_COMMIT_SHA",
    "SOURCE_VERSION",
)

BRANCH_VARIABLES = ("RENDER_GIT_BRANCH", "VERCEL_GIT_COMMIT_REF")


def test_the_commit_is_reported_when_the_host_says_which_one(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0f1e2d3c4b5a69788796a5b4c3d2e1f00998877")
    assert deployed_commit() == "0f1e2d3c4b5a69788796a5b4c3d2e1f00998877"


def test_vercels_variable_is_read_too(monkeypatch):
    for name in COMMIT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "abc1234")
    assert deployed_commit() == "abc1234"


def test_nothing_is_invented_when_the_host_says_nothing(monkeypatch):
    for name in COMMIT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    assert deployed_commit() is None


def test_an_empty_variable_is_not_a_commit(monkeypatch):
    for name in COMMIT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "   ")
    assert deployed_commit() is None


def test_the_branch_is_reported_when_the_host_says_which_one(monkeypatch):
    for name in BRANCH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_BRANCH", "main")
    assert deployed_branch() == "main"

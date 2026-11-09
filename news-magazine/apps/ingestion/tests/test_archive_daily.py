from __future__ import annotations

from types import SimpleNamespace

from scripts import archive_daily


class _FailingCall:
    def __init__(self, message: str):
        self._message = message

    def execute(self):
        raise Exception(self._message)  # noqa: BLE001


class _FailingDb:
    def __init__(self, message: str):
        self._message = message

    def rpc(self, *_args, **_kwargs):
        return _FailingCall(self._message)


def test_a_supabase_egress_quota_lockout_is_non_fatal(monkeypatch):
    monkeypatch.setattr(archive_daily, "get_settings", lambda: SimpleNamespace(retention_hours=48))
    monkeypatch.setattr(
        archive_daily,
        "get_db",
        lambda: _FailingDb(
            "Service for this project is restricted due to exceed_egress_quota"
        ),
    )

    assert archive_daily.main() == 0


def test_an_unexpected_database_error_still_fails(monkeypatch):
    monkeypatch.setattr(archive_daily, "get_settings", lambda: SimpleNamespace(retention_hours=48))
    monkeypatch.setattr(
        archive_daily,
        "get_db",
        lambda: _FailingDb("database unavailable"),
    )

    assert archive_daily.main() == 2

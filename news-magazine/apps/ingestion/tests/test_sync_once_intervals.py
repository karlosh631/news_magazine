"""
The sweep must ask each publisher as often as it agreed to be asked.

THE RUN THAT ASKED TOO OFTEN
----------------------------
The GitHub Actions pass used to ask EVERY outlet on EVERY pass. The
pass runs every three minutes now, so an outlet whose row says "every
300 seconds" was being asked every 180 - and the row is a number this
project agreed with that publisher, not a suggestion.

The rule now: an outlet is polled when its own interval has elapsed,
and left alone until then. `last_success_at` is written by every
poller the moment a poll of that outlet finishes without failing, so
the CI pass and the long-lived worker take turns rather than both
asking the same publisher at the same moment.

These specs pin that rule, and the floor under it: an interval of zero
still cannot turn the sweep into a load test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.sync_once import source_interval, source_is_due

NOW = datetime(2026, 10, 23, 15, 0, 0, tzinfo=timezone.utc)


def source(**over) -> dict:
    row = dict(
        name="An Outlet",
        poll_interval_seconds=90,
        last_success_at=None,
    )
    row.update(over)
    return row


def ago(seconds: int) -> str:
    return (NOW - timedelta(seconds=seconds)).isoformat()


class TestSourceInterval:
    def test_the_outlets_own_number_is_used(self):
        assert source_interval(source(poll_interval_seconds=90)) == 90
        assert source_interval(source(poll_interval_seconds=300)) == 300

    def test_the_floor_is_never_crossed(self):
        # A row that says zero, or says nothing, cannot make the sweep
        # sub-minute. MIN_POLL_SECONDS is 60.
        assert source_interval(source(poll_interval_seconds=0)) == 60
        assert source_interval(source(poll_interval_seconds=5)) == 60
        assert source_interval(source(poll_interval_seconds=None)) == 60

    def test_a_nonsense_value_is_treated_as_no_interval(self):
        assert source_interval(source(poll_interval_seconds="soon")) == 60


class TestSourceIsDue:
    def test_an_outlet_that_has_never_been_polled_is_due(self):
        assert source_is_due(source(), NOW) is True

    def test_an_outlet_polled_a_second_ago_is_not_due(self):
        row = source(last_success_at=ago(1))
        assert source_is_due(row, NOW) is False

    def test_an_outlet_polled_long_enough_ago_is_due(self):
        row = source(last_success_at=ago(200))
        assert source_is_due(row, NOW) is True

    def test_the_slow_outlets_are_left_alone(self):
        # The five publishers that refuse this worker are on a five
        # minute interval. A pass every three minutes must leave them
        # alone on the pass in between.
        row = source(poll_interval_seconds=300, last_success_at=ago(180))
        assert source_is_due(row, NOW) is False

        row = source(poll_interval_seconds=300, last_success_at=ago(360))
        assert source_is_due(row, NOW) is True

    def test_a_failed_poll_keeps_the_old_timestamp(self):
        # The store path leaves `last_success_at` alone when a poll
        # fails, so a broken outlet is retried at the next pass rather
        # than waiting out an interval it never served.
        row = source(last_success_at=ago(10))
        assert source_is_due(row, NOW) is False

    def test_a_timestamp_with_a_z_is_read(self):
        row = source(last_success_at="2026-10-23T14:59:30Z")
        assert source_is_due(row, NOW) is False

    def test_an_unreadable_timestamp_is_due(self):
        # A row with a broken timestamp must not be able to silence an
        # outlet for ever.
        row = source(last_success_at="not a date")
        assert source_is_due(row, NOW) is True

    def test_a_naive_timestamp_is_read_as_utc(self):
        row = source(last_success_at="2026-10-23T14:59:30")
        assert source_is_due(row, NOW) is False

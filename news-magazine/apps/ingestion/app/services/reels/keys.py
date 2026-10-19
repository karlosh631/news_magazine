"""
API key rotation with failover.

Why this exists
---------------
Free-tier video APIs meter requests per key. A key that hits its quota
returns 429 for an hour, and a single-key client treats that as "the
provider is down" and stops producing reels for everyone behind it.
Rotating to the next configured key is the difference between a
category that degrades and one that dies.

The contract
------------
- `KeyPool` only ever uses keys the operator configured. It does not
  discover, scrape or borrow credentials from anywhere. A key found in
  a public repo is somebody else's credential; using it is abuse, it
  gets revoked, and it takes this project down with it.
- A key is only skipped for a QUOTA reason. A 500 or a timeout is the
  provider's problem and every key will hit it, so burning the pool on
  transient errors is how you end up with zero keys left before the
  provider recovers.
- A key that stays exhausted stays skipped until `reset_cooldowns()`.
  Re-trying a key known to be out of quota is how one bad hour becomes
  a whole-hour outage.

Usage
-----
    pool = KeyPool("pexels", ["key-a", "key-b"])
    async for key in pool:
        response = await get(key)
        pool.mark(key, ok=response.status_code == 200)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("reels.keys")


class KeyPoolExhausted(RuntimeError):
    """
    Every configured key is in cooldown.

    Distinct from a provider error: nothing is wrong with the provider,
    we simply have no quota left. Callers should back off and try again
    after the shortest cooldown rather than reporting an outage.
    """


@dataclass
class _KeyState:
    key: str
    # Unix timestamp this key becomes usable again. 0 means usable now.
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    total_uses: int = 0

    def available(self, now: float) -> bool:
        return now >= self.cooldown_until


@dataclass
class KeyPool:
    """
    A rotating set of API keys for one provider.

    `cooldown_seconds` is how long an exhausted key is skipped. It
    defaults to an hour because that is the shortest window every free
    tier we target actually resets on.
    """

    source_key: str
    keys: list[str]
    cooldown_seconds: float = 3600.0

    _states: list[_KeyState] = field(default_factory=list, init=False, repr=False)
    _cursor: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        # Empty and whitespace-only entries are dropped: a trailing comma
        # in a comma-separated env var is a common way to end up with a
        # pool that looks configured and is not.
        cleaned = [k.strip() for k in self.keys if k and k.strip()]
        self._states = [_KeyState(key=k) for k in cleaned]

    # -------------------------------------------------
    # Introspection
    # -------------------------------------------------

    def __len__(self) -> int:
        return len(self._states)

    @property
    def is_empty(self) -> bool:
        return not self._states

    def available_count(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        return sum(1 for state in self._states if state.available(now))

    def snapshot(self) -> list[dict]:
        """Per-key state, for /api/health. No key material is included."""
        now = time.time()
        return [
            {
                # A short prefix is enough to tell two keys apart in a
                # log without printing a working credential.
                "label": state.key[:6] + "…",
                "available": state.available(now),
                "cooldown_remaining_s": max(0, int(state.cooldown_until - now)),
                "consecutive_failures": state.consecutive_failures,
                "total_uses": state.total_uses,
            }
            for state in self._states
        ]

    # -------------------------------------------------
    # Rotation
    # -------------------------------------------------

    def next_key(self) -> str:
        """
        The next usable key, round-robin from the cursor.

        Raises KeyPoolExhausted when every key is in cooldown. The
        message carries how long to wait, because the correct caller
        behaviour is a sleep, not a retry storm.
        """
        now = time.time()
        total = len(self._states)

        for offset in range(total):
            index = (self._cursor + offset) % total
            state = self._states[index]
            if state.available(now):
                self._cursor = (index + 1) % total
                state.total_uses += 1
                return state.key

        soonest = min((s.cooldown_until for s in self._states), default=now)
        wait = max(1, int(soonest - now))
        raise KeyPoolExhausted(
            f"All {total} key(s) for {self.source_key} are rate limited. "
            f"Next key frees up in {wait}s. Add another key, or wait."
        )

    # -------------------------------------------------
    # Feedback
    # -------------------------------------------------

    def mark(self, key: str, *, ok: bool) -> None:
        """
        Record the outcome of a request made with `key`.

        Only quota failures cool a key down. A 500 is retried on the
        next key immediately, because it is the provider failing, not
        the credential.
        """
        state = self._state_for(key)
        if state is None:
            return

        if ok:
            state.consecutive_failures = 0
            state.cooldown_until = 0.0
            return

        state.consecutive_failures += 1

    def mark_quota_exceeded(self, key: str, retry_after: float | None = None) -> None:
        """
        Put `key` in cooldown after a 429 / quota error.

        `retry_after` is honoured when the provider sends one, because
        it is authoritative and guessing an hour when the provider says
        "come back in 90 seconds" wastes quota.
        """
        state = self._state_for(key)
        if state is None:
            return

        cooldown = self.cooldown_seconds
        if retry_after and 0 < retry_after <= self.cooldown_seconds * 2:
            cooldown = retry_after

        state.cooldown_until = time.time() + cooldown
        state.consecutive_failures = 0

        logger.warning(
            "%s: key %s… rate limited, skipping for %ds (%d key(s) still usable).",
            self.source_key, key[:6] + "…", int(cooldown),
            self.available_count(),
        )

    def reset_cooldowns(self) -> None:
        """
        Make every key usable again.

        Called on a long timer so a quota that resets early is picked
        up without a redeploy, and so a mistyped key that spent an hour
        in cooldown does not stay there forever.
        """
        restored = 0
        for state in self._states:
            if state.cooldown_until:
                state.cooldown_until = 0.0
                state.consecutive_failures = 0
                restored += 1
        if restored:
            logger.info("%s: restored %d key(s) from cooldown.", self.source_key, restored)

    def _state_for(self, key: str) -> _KeyState | None:
        for state in self._states:
            if state.key == key:
                return state
        return None


def build_pool(source_key: str, raw: str | None) -> KeyPool:
    """
    Build a pool from a comma-separated configuration value.

    Accepts `a,b,c` and also newlines, because pasting a block of keys
    into a Render secret is at least as common as typing them.
    """
    if not raw:
        return KeyPool(source_key=source_key, keys=[])

    parts = [p.strip() for p in raw.replace("\n", ",").replace("\r", ",").split(",")]
    return KeyPool(source_key=source_key, keys=[p for p in parts if p])
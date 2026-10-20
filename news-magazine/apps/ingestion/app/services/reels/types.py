"""
Shared shapes for the reels pipeline.

`Reel` is the one currency every provider adapter emits and the store
consumes. Adapters do not talk to the database and the store does not
talk to the network; both sides meet here, which is what lets a new
provider be added without touching either.

`ReelSource` is the adapter contract. It is deliberately small — fetch,
name, whether it needs a key — because every extra method is another
thing a new provider has to get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class Reel:
    """
    One short video, as metadata.

    Nothing here is required except identity and a playable URL. Every
    other field is filled in when the provider supplies it and left
    None when it does not — the UI distinguishes "no creator known"
    from "no creator exists", which a non-optional str would erase.
    """

    source_key: str
    external_id: str

    # Direct, playable, on the provider's own CDN. Never proxied through
    # us and never stored — the browser fetches this URL directly.
    video_url: str

    # Set ONLY by providers that cannot offer a directly playable file
    # and must hand the browser a player instead. YouTube is the one
    # today: its media lives behind a signature we deliberately do not
    # decode, so `video_url` is the official embed player src and this
    # repeats it to say "render an <iframe>, not a <video>".
    #
    # None means a plain media file, which is every other provider and
    # every row written before this field existed. The web player
    # branches on presence, not on source_key, so a new provider gets
    # the right player by setting one field.
    embed_url: str | None = None

    poster_url: str | None = None
    title: str | None = None
    description: str | None = None

    # Attribution. Populated by every adapter, whether or not the
    # licence requires it to be displayed.
    creator_name: str | None = None
    creator_url: str | None = None
    license_name: str | None = None
    license_url: str | None = None

    # True when the licence requires credit be shown. False means "not
    # required" — never "you may claim it as yours".
    attribution_required: bool = True

    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    published_at: str | None = None

    def __post_init__(self) -> None:
        if not self.source_key:
            raise ValueError("Reel.source_key is required")
        if not self.external_id:
            raise ValueError("Reel.external_id is required")
        if not self.video_url.startswith(("http://", "https://")):
            # A relative or malformed URL produces a silent black card
            # in the feed, which is exactly the bug we are avoiding.
            raise ValueError(f"Reel.video_url must be absolute: {self.video_url!r}")

    @property
    def aspect_ratio(self) -> float | None:
        """width / height, or None when the provider did not say."""
        if not self.width or not self.height or self.height == 0:
            return None
        return self.width / self.height


@runtime_checkable
class ReelSource(Protocol):
    """
    What a provider adapter must provide.

    `fetch` returns candidates; it does not store them and it does not
    decide whether they are new. Keeping the network and the database
    apart is what makes a provider swappable and the store testable.
    """

    #: Stable identifier written to reels.source_key. Never change one
    #: after rows exist — it is half of the dedup key.
    key: str

    #: Whether this provider cannot work without an API key. Sources
    #: where this is False are what keep the category alive with no
    #: configuration at all.
    requires_key: bool

    async def fetch(self, limit: int = 30) -> list[Reel]:
        """Return up to `limit` candidate reels, newest first."""
        ...


@dataclass
class SourceHealth:
    """Per-provider outcome for one ingest pass. Reported, not hidden."""

    source_key: str
    fetched: int = 0
    stored: int = 0
    skipped: int = 0
    error: str | None = None
    keys_used: int = 0
    notes: list[str] = field(default_factory=list)
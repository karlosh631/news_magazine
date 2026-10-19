"""
Reels: vertical short-form video for the Reels category.

What this is, precisely
-----------------------
A separate ingestion path from the outlet scrapers. Outlets give us
articles with images; this gives us short vertical video. The two share
the database and nothing else — a reel is never written to `articles`,
and an article is never written here.

The rule that shapes every file in this package: no video bytes are
stored, ever. A reel row is metadata plus an upstream URL, and playback
streams from the provider's CDN. Reels are the largest objects in the
system; putting them in Postgres would bloat the database and blow
through bandwidth limits to buy nothing.

Attribution is a first-class column, not a UI nicety. Every adapter
fills in creator/licence regardless of whether its licence requires
display, because "this provider does not require credit" is not the
same claim as "you may present this as your own", and only the first
one is true.
"""

from app.services.reels.types import Reel, ReelSource
from app.services.reels.keys import KeyPool, KeyPoolExhausted

__all__ = ["Reel", "ReelSource", "KeyPool", "KeyPoolExhausted"]
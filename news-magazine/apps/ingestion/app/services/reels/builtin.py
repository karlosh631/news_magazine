"""
The built-in reels provider: this site's own news.

What this is
------------
Every provider in `providers.py` is a SEARCH over somebody else's
catalogue, and a search over a finished catalogue stops producing
anything: the same hundred results come back for ever, the reels table
stops growing, and the category page says "245 clips in the catalogue,
0 clips fetched today" while the ingestion log says the pass was fine.

`ArticlesSource` is the one that cannot do that, because it does not
search: it reads the articles this site has already published and turns
the newest ones with a verified photograph into reels. A new article is
a new reel by construction, so the Reels page moves in step with the
news page and no configuration is needed for it to work.

Why this module exists
----------------------
The provider is an adapter and the store is the thing that writes, and
neither is supposed to know about the other: `store.py` discovers a
source's key pool by duck-typing precisely so that it keeps working
with an adapter written before it. This module is the seam. It is the
only place that knows about both, and it exists so that the pass can
be RUN on its own — from the cron, which is the path that is
guaranteed to run — without the store having to import a concrete
adapter to do it.

    from app.services.reels.builtin import ingest_article_reels

    health = await ingest_article_reels(db)

The long-lived worker runs the same function through the scheduler, so
the two paths cannot drift.
"""

from __future__ import annotations

import logging

from app.services.reels.providers import ArticlesSource
from app.services.reels.store import ingest_reels
from app.services.reels.types import SourceHealth

logger = logging.getLogger("reels.builtin")


def _budget(per_source: int | None) -> int:
    """
    How many clips this provider may contribute to this pass.

    The same two caps the scheduler applies, read here as well: one
    provider's share of a pass, and the whole pass's budget. A cap
    that is only enforced in one of the two places it is reached is not
    a cap.
    """
    try:
        from app.config import get_settings

        settings = get_settings()
        cap = int(per_source or settings.max_reels_per_provider or 0)
        cycle = int(settings.max_reels_per_cycle or 0)
    except Exception:  # noqa: BLE001 - a missing config is not a failure
        logger.debug(
            "reels: the settings could not be read; using the default budget."
        )
        return max(int(per_source or 0), 0)

    if cap <= 0:
        cap = 30
    if cycle > 0:
        cap = min(cap, cycle)
    return max(cap, 1)


async def ingest_article_reels(
    db,
    *,
    per_source: int | None = None,
) -> list[SourceHealth]:
    """
    One pass of the built-in provider. Never raises.

    Returns the same health report every other provider produces, so
    the caller can print it, record it or ignore it exactly as it does
    for the rest of the pass.
    """
    budget = _budget(per_source)

    # The adapter needs no key and makes no HTTP request, so it is
    # built without either. `build_source` would do the same thing; it
    # is not used here because the provider is not configurable — there
    # is nothing to configure.
    source = ArticlesSource(pool=None, client=None, hint="")

    return await ingest_reels(db, sources={source.key: source}, per_source=budget)

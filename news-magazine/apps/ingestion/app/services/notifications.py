"""
Tell the readers that there is news.

WHAT THIS IS FOR
----------------
A reader who has asked to be told when the news arrives should be
told, and the moment to tell them is the moment a story is stored —
which is here, at the end of a pass that stored something.

The site is the reader's own: it knows what it published, and it knows
the devices that asked to hear about it. So the pass tells the site,
and the site raises the notification on each device.

WHY IT CANNOT BE A FAILURE
---------------------------
A notification that cannot be sent is not a reason to fail a pass that
fetched two hundred stories. Everything here is wrapped so that the
worst case is a line in the log and the pass carries on.

WHAT IT NEEDS
-------------
The web app's address and the shared secret the worker already has.
Nothing new: the worker and the site already trust each other.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: How long the call may take. It is a courtesy ping at the end of a
#: pass, and a pass that waits a minute for a notification is a pass
#: that is a minute late.
NOTIFY_TIMEOUT_SECONDS = 15.0


async def notify_new_news(
    new_count: int,
    settings: Any,
    title: str | None = None,
    body: str | None = None,
) -> bool:
    """
    Tell the site that there is news, so it can tell the readers.

    Returns True when the site was told, False when it was not - and
    False is not an error, it is a site that has no push path
    configured, which is an ordinary state.
    """
    if new_count <= 0:
        return False

    if not getattr(settings, "push_notifications_enabled", True):
        return False

    base = (getattr(settings, "web_app_url", "") or "").strip().rstrip("/")
    if not base:
        return False

    # Both of the secrets the worker and the site share. The site may
    # have either of them and not the other, so both are presented and
    # either one is enough.
    headers = {
        "x-ingestion-secret": (
            getattr(settings, "ingestion_secret", "") or ""
        ).strip(),
        "x-cron-secret": (getattr(settings, "cron_secret", "") or "").strip(),
    }
    if not any(headers.values()):
        return False

    url = f"{base}/api/notifications/push"
    payload: dict[str, Any] = {"count": new_count}
    if title:
        payload["title"] = title
    if body:
        payload["body"] = body

    try:
        async with httpx.AsyncClient(timeout=NOTIFY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
            )
    except Exception as exc:  # noqa: BLE001 — a ping must never fail a pass
        logger.info("The news notification could not be sent: %s", str(exc)[:160])
        return False

    if response.status_code >= 400:
        logger.info(
            "The news notification was refused (%s).", response.status_code
        )
        return False

    try:
        answer = response.json()
    except Exception:  # noqa: BLE001
        return True

    if isinstance(answer, dict) and answer.get("configured") is False:
        # The push path is not set up. That is not a failure: the
        # reader's own notification - the one the browser raises from
        # the page - does not need any of this.
        logger.info(
            "The push path is not configured; the site's own notification "
            "still works."
        )
        return True

    sent = answer.get("sent") if isinstance(answer, dict) else None
    logger.info("The news notification was sent to %s device(s).", sent)
    return True

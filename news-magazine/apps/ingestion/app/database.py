"""
Thin wrapper around the Supabase client using the SERVICE ROLE key.

This key bypasses Row Level Security by design — that's what lets the
ingestion worker write articles/sync logs without a user session. It must
NEVER be sent to a browser or logged. It only ever lives in this process's
environment (set via Render's environment variables, not committed).

THE SESSION IS REPLACED, AND THAT IS THE POINT
------------------------------------------------
postgrest-py builds its HTTP session with `http2=True`, hard-coded:

    def create_session(self, base_url, headers, timeout, verify, proxy):
        return SyncClient(..., follow_redirects=True, http2=True)

supabase-py 2.10 offers no way to change it — `ClientOptions` has no
`httpx_client` field, and `_init_postgrest_client` takes no session
argument. So the client speaks HTTP/2 to PostgREST, and over HTTP/2 the
server's connection is dropped between calls. When the next call reuses
that connection, httpcore raises

    httpcore.RemoteProtocolError: Server disconnected

which is not an answer about the data. It is a dead socket.

The cost of that was measured, not guessed. In the run of 2026-10-23
17:23 UTC the pass logged 89 of them inside its twenty-five minute
window, plus eighteen dedupe lookups that gave up and inserted anyway.
Each one cost four attempts and 0.4 + 1.2 + 3.6 seconds of sleeping
before the work carried on — around eight minutes of the pass spent
waiting for a socket, and eighteen stories inserted on a lookup that had
already failed.

So the session is replaced with one that speaks HTTP/1.1, keeps a small
number of connections, and lets them go quickly. HTTP/1.1 over
PostgREST is what every other client in this project already speaks
(the web app uses postgrest-js over HTTP/1.1), and a connection that
has been idle for five seconds is simply not reused.

Nothing else about the client changes: the same URL, the same key, the
same headers, the same timeout.
"""
from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.config import get_settings

# How long one database call may take before it is a failure.
DB_TIMEOUT_SECONDS = 120.0

# How long to wait for the socket itself.
DB_CONNECT_TIMEOUT_SECONDS = 20.0

# How many connections the pool may hold open at once.
#
# It must be AT LEAST the number of threads that can be in a database
# call at once, or the pool itself becomes the bottleneck: DB_EXECUTOR
# has sixteen workers and the event loop thread also issues queries (the
# dedup lookup), so seventeen requests can be outstanding at once. With
# fewer connections than that a request waits for a free socket, and the
# pool timeout is the client timeout - 120 seconds - so the whole pass
# queues behind the pool.
DB_MAX_CONNECTIONS = 24

# How many of those may be kept alive between calls.
DB_MAX_KEEPALIVE_CONNECTIONS = 12

# How long a kept-alive connection may sit unused before it is dropped.
#
# Thirty seconds. The pass spends most of its time fetching publisher
# pages, so a five-second expiry means a fresh TLS handshake for nearly
# every database call - that is the reconnect churn, and it is a cost
# paid on every single lookup. Thirty seconds is well inside the edge's
# own idle timeout and long enough that connections are actually
# reused.
DB_KEEPALIVE_EXPIRY_SECONDS = 30.0


def _build_session(old: httpx.Client) -> httpx.Client:
    """A session that talks HTTP/1.1 to the same place, with the same headers."""
    return httpx.Client(
        base_url=old.base_url,
        headers=dict(old.headers),
        timeout=httpx.Timeout(
            DB_TIMEOUT_SECONDS, connect=DB_CONNECT_TIMEOUT_SECONDS
        ),
        follow_redirects=True,
        # The one line that matters. See the note at the top of the file.
        http2=False,
        limits=httpx.Limits(
            max_connections=DB_MAX_CONNECTIONS,
            max_keepalive_connections=DB_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=DB_KEEPALIVE_EXPIRY_SECONDS,
        ),
    )


def _install_resilient_session(client: Client) -> None:
    """Swap the postgrest session for one that does not drop connections."""
    postgrest = client.postgrest
    old = postgrest.session
    new = _build_session(old)
    postgrest.session = new
    try:
        old.close()
    except Exception:  # pragma: no cover - closing a dead socket is best effort
        pass


@lru_cache
def get_db() -> Client:
    settings = get_settings()
    client = create_client(
        settings.supabase_url, settings.supabase_service_role_key
    )
    _install_resilient_session(client)
    return client


def reset_db_session() -> None:
    """
    DEPRECATED - and deliberately not called from anywhere.

    It was written to replace a dead connection after a transport
    failure. It must not be used: the session is SHARED, and five source
    polls and sixteen executor threads are using it at the same time.
    Closing it makes every in-flight request fail with

        RuntimeError: Cannot send a request, as the client has been closed.

    which is not a transient error, so it is not retried, and the
    articles in flight are lost. One transport failure in one thread
    would destroy every concurrent database request.

    There is no need for it anyway. With HTTP/1.1, httpcore drops the
    failed connection from the pool by itself - the pool checks whether
    a connection is closed or expired before it hands one out - so the
    next attempt opens a fresh one without anybody asking.
    """
    raise NotImplementedError(
        "The database session is shared; closing it breaks every request "
        "in flight. httpcore already replaces a failed connection."
    )

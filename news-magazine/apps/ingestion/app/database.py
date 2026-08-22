"""
Thin wrapper around the Supabase client using the SERVICE ROLE key.

This key bypasses Row Level Security by design — that's what lets the
ingestion worker write articles/sync logs without a user session. It must
NEVER be sent to a browser or logged. It only ever lives in this process's
environment (set via Render's environment variables, not committed).
"""
from functools import lru_cache
from supabase import create_client, Client

from app.config import get_settings


@lru_cache
def get_db() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)

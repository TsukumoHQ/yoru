"""Pluggable data layer.

The whole SaaSForge surface (orgs, workspaces, subscriptions, plans,
notifications, invitations, groups, webhooks…) talks to a uniform table-CRUD
API that historically only had one implementation: Supabase/PostgREST.

`get_data_store()` returns either:

* ``LocalDataStore`` — a self-contained, schemaless document store in the app
  database. No external service. This is the default and is what lets the whole
  dashboard run locally.
* ``SupabaseManager`` — the hosted PostgREST backend (when
  ``AUTH_PROVIDER=supabase``).

Both expose the same methods (``get_record``, ``query_records``,
``insert_record``, ``update_record``, ``upsert_record``, ``delete_record``,
``count_records``, ``count_records_cached``), so every service is backend-blind.
"""
from __future__ import annotations

import os
from typing import Any

_INSTANCE_CACHE: dict[str, Any] = {}


def get_data_store(access_token: str | None = None, **kwargs: Any):
    """Return a data store. Cached per (backend, access_token).

    ``access_token`` matters only for the Supabase backend (RLS scoping); the
    local store is single-tenant and ignores it.
    """
    backend = os.getenv("AUTH_PROVIDER", "local").strip().lower()
    if backend == "supabase":
        # Per-token instances so RLS scoping stays correct.
        from libs.supabase.supabase import SupabaseManager

        return SupabaseManager(access_token=access_token, **kwargs)

    # Local: one shared, stateless instance.
    if "local" not in _INSTANCE_CACHE:
        from libs.datastore.local_store import LocalDataStore

        _INSTANCE_CACHE["local"] = LocalDataStore()
    return _INSTANCE_CACHE["local"]


def reset_data_store() -> None:
    _INSTANCE_CACHE.clear()

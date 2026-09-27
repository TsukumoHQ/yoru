"""is_installed() must not fail open on a transient DB error (task 78077378,
AC2).

Before: any exception querying auth_users — a missing table (genuinely
fresh install) OR a connection drop/timeout on an already-installed
instance — returned `installed=False`, which unlocks /setup/init
(`ensure_not_installed()` only raises when `is_installed()` is True).
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from apps.api.api.services.setup import setup_service as setup_service_mod
from apps.api.api.services.setup.setup_service import SetupService


class _RaisingSession:
    """Stands in for `Session(app_engine)` and raises on `.exec(...)`."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __enter__(self) -> _RaisingSession:
        return self

    def __exit__(self, *exc_info) -> None:
        return None

    def exec(self, *_a, **_k):
        raise self._exc


def _table_missing_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("no such table: auth_users"))


def _transient_error() -> OperationalError:
    return OperationalError(
        "SELECT 1", {}, Exception("SSL connection has been closed unexpectedly")
    )


def test_fresh_install_missing_table_is_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(setup_service_mod, "Session", lambda _e: _RaisingSession(_table_missing_error()))
    assert SetupService().is_installed() is False


def test_transient_db_error_does_not_fail_open(monkeypatch) -> None:
    monkeypatch.setattr(setup_service_mod, "Session", lambda _e: _RaisingSession(_transient_error()))
    with pytest.raises(OperationalError):
        SetupService().is_installed()


def test_transient_db_error_keeps_setup_init_locked(monkeypatch) -> None:
    """ensure_not_installed() must not silently unlock /setup/init just
    because the DB blipped — it should surface the error, not treat the
    instance as uninstalled."""
    monkeypatch.setattr(setup_service_mod, "Session", lambda _e: _RaisingSession(_transient_error()))
    with pytest.raises(OperationalError):
        SetupService().ensure_not_installed()


def test_supabase_provider_always_installed(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "supabase")
    assert SetupService().is_installed() is True

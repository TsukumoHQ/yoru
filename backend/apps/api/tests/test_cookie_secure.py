"""vuln-0004 (strix pilot bb1f35fc, pinned): the session/refresh cookies must
carry Secure outside dev. ``_cookie_secure()`` must force Secure when
``ENVIRONMENT`` is prod/production, regardless of ``COOKIE_SECURE`` — a prod
deploy (fly.toml sets ``ENVIRONMENT=production``) must not be able to
silently ship insecure cookies by omitting ``COOKIE_SECURE``.
"""
from __future__ import annotations

from apps.api.api.routers.auth.cookie_router import _cookie_secure


def test_dev_default_is_insecure(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert _cookie_secure() is False


def test_cookie_secure_env_opts_in_outside_prod(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    assert _cookie_secure() is True


def test_production_environment_forces_secure_even_if_cookie_secure_unset(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert _cookie_secure() is True


def test_prod_alias_forces_secure(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert _cookie_secure() is True

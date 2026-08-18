"""Canonical VCS-provider registry — the single source of truth for the git
hosts yoru recognizes and the provider slug each one maps to.

A provider is a stable lowercase slug (``github`` | ``gitlab`` | ``bitbucket``
| ``azure``). Everything provider-aware — public-session redaction, the
``?vcs=`` search filter, the per-provider integration surfaces — derives from
this one table, so adding a provider is a one-line change here instead of a
repo-wide grep. Host detection is deliberately host-based (not owner/repo), so
it stays valid for self-hosted instances that never touch a cloud integration.
"""
from __future__ import annotations

import re

# provider slug -> the git hosts that belong to it (lowercased, no scheme).
_PROVIDER_HOSTS: dict[str, tuple[str, ...]] = {
    "github": ("github.com",),
    "gitlab": ("gitlab.com",),
    "bitbucket": ("bitbucket.org",),
    # Azure DevOps ships two remote hosts (https vs ssh).
    "azure": ("dev.azure.com", "ssh.dev.azure.com"),
}

# Stable, ordered tuple of the provider slugs yoru understands.
KNOWN_PROVIDERS: tuple[str, ...] = tuple(_PROVIDER_HOSTS.keys())

# host -> provider slug (reverse index, built once).
_HOST_TO_PROVIDER: dict[str, str] = {
    host: provider
    for provider, hosts in _PROVIDER_HOSTS.items()
    for host in hosts
}


def all_hosts() -> tuple[str, ...]:
    """Every known git host, deduped, in registration order — used to build the
    public-redaction allowlist regex."""
    return tuple(_HOST_TO_PROVIDER.keys())


def hosts_for(provider: str | None) -> tuple[str, ...]:
    """The git hosts that belong to ``provider`` (empty tuple if unknown)."""
    if not provider:
        return ()
    return _PROVIDER_HOSTS.get(provider.strip().lower(), ())


def is_known_provider(provider: str | None) -> bool:
    return bool(provider) and provider.strip().lower() in _PROVIDER_HOSTS


def provider_for_host(host: str | None) -> str | None:
    """Map a bare git host (e.g. ``bitbucket.org``) to its provider slug, or
    ``None`` for a host we don't recognize. Tolerates a leading ``www.`` and a
    trailing port."""
    if not host:
        return None
    h = host.strip().lower()
    if h.startswith("www."):
        h = h[4:]
    h = h.split(":", 1)[0]  # drop any :port
    return _HOST_TO_PROVIDER.get(h)


def _host_of(git_remote: str | None) -> str | None:
    """Extract just the host from a git remote URL. Handles both the
    ``git@host:Owner/Repo.git`` and ``https|ssh://host/Owner/Repo`` shapes —
    the same two forms events_router._parse_git_remote accepts."""
    if not git_remote:
        return None
    s = git_remote.strip()
    if s.startswith("git@"):
        rest = s[4:]
        if ":" not in rest:
            return None
        return rest.split(":", 1)[0]
    m = re.match(r"(?:https?|ssh)://(?:[^@/]+@)?([^/]+)/", s)
    return m.group(1) if m else None


def provider_of_remote(git_remote: str | None) -> str | None:
    """Map a full git remote URL to its provider slug, or ``None`` when the
    remote is empty, unparseable, or points at a host we don't recognize (a
    self-hosted GitLab/Gitea, for instance)."""
    return provider_for_host(_host_of(git_remote))

"""Unit tests for the canonical VCS-provider registry (vcs.py).

Pure functions — no DB, no fixtures. Locks the provider↔host mapping the
search-by-VCS filter, the public redaction regex, and the Bitbucket/GitHub
integrations all derive from.
"""
from __future__ import annotations

import pytest

from apps.api.api.routers.receipt import vcs


def test_known_providers_include_bitbucket():
    assert set(vcs.KNOWN_PROVIDERS) == {"github", "gitlab", "bitbucket", "azure"}


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("git@bitbucket.org:acme/app.git", "bitbucket"),
        ("https://bitbucket.org/acme/app", "bitbucket"),
        ("https://user@bitbucket.org/acme/app.git", "bitbucket"),
        ("git@github.com:acme/app.git", "github"),
        ("https://github.com/acme/app", "github"),
        ("git@gitlab.com:acme/app.git", "gitlab"),
        ("git@ssh.dev.azure.com:v3/org/proj/repo", "azure"),
        ("https://dev.azure.com/org/proj/_git/repo", "azure"),
    ],
)
def test_provider_of_remote_maps_known_hosts(remote, expected):
    assert vcs.provider_of_remote(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        None,
        "",
        "not-a-url",
        "git@git.selfhosted.example.com:acme/app.git",  # unknown host
        "https://gitea.internal/acme/app",
    ],
)
def test_provider_of_remote_unknown_is_none(remote):
    assert vcs.provider_of_remote(remote) is None


def test_provider_for_host_tolerates_www_and_port():
    assert vcs.provider_for_host("www.bitbucket.org") == "bitbucket"
    assert vcs.provider_for_host("bitbucket.org:443") == "bitbucket"
    assert vcs.provider_for_host("BitBucket.org") == "bitbucket"
    assert vcs.provider_for_host("unknown.example") is None
    assert vcs.provider_for_host(None) is None


def test_hosts_for_and_is_known_provider():
    assert vcs.hosts_for("bitbucket") == ("bitbucket.org",)
    assert vcs.hosts_for("azure") == ("dev.azure.com", "ssh.dev.azure.com")
    assert vcs.hosts_for("nope") == ()
    assert vcs.hosts_for(None) == ()
    assert vcs.is_known_provider("bitbucket") is True
    assert vcs.is_known_provider("BITBUCKET") is True
    assert vcs.is_known_provider("mercurial") is False
    assert vcs.is_known_provider(None) is False


def test_all_hosts_covers_every_provider_host():
    hosts = vcs.all_hosts()
    assert "bitbucket.org" in hosts
    # every host resolves back to a known provider (no orphans).
    for h in hosts:
        assert vcs.provider_for_host(h) in vcs.KNOWN_PROVIDERS

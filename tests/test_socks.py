"""Tests for the SOCKS/Tor transport helper.

These are fast unit tests that do not require a running Tor daemon.
They verify that `backend.core.socks.make_async_client()` constructs the
right httpx transport based on the `JAMBU_TOR_SOCKS_URL` env var.
"""

from __future__ import annotations

import os
from typing import Generator
from unittest import mock

import httpx
import pytest


@pytest.fixture(autouse=True)
def _clear_socks_env() -> Generator[None, None, None]:
    """Ensure each test starts with a clean SOCKS env."""
    with mock.patch.dict(
        os.environ,
        {"JAMBU_TOR_SOCKS_URL": "", "AGENT_VPN_PROXY": ""},
        clear=False,
    ):
        yield


def _reload_socks_module():
    """Re-import backend.core.socks so env-var changes take effect.

    The module caches `get_socks_url()` results via `os.environ.get`,
    so we need a fresh module object per env configuration.
    """
    import importlib
    from backend.core import socks as socks_module

    return importlib.reload(socks_module)


def test_make_async_client_without_proxy_is_plain_httpx():
    """When no SOCKS URL is configured, make_async_client behaves exactly
    like httpx.AsyncClient (default transport, no proxy overhead).
    """
    socks = _reload_socks_module()
    assert socks.is_tor_enabled() is False

    client = socks.make_async_client()
    assert isinstance(client, httpx.AsyncClient)
    # Default httpx.AsyncClient uses an AsyncHTTPTransport instance.
    assert type(client._transport).__name__ == "AsyncHTTPTransport"


def test_make_async_client_with_proxy_uses_socks_transport():
    """When JAMBU_TOR_SOCKS_URL is set, make_async_client returns a client
    whose transport is an httpx-socks AsyncProxyTransport.
    """
    httpx_socks = pytest.importorskip("httpx_socks")

    with mock.patch.dict(os.environ, {"JAMBU_TOR_SOCKS_URL": "socks5h://127.0.0.1:9150"}):
        socks = _reload_socks_module()
        assert socks.is_tor_enabled() is True
        assert socks.get_socks_url() == "socks5h://127.0.0.1:9150"

        client = socks.make_async_client()
        assert isinstance(client, httpx.AsyncClient)
        assert isinstance(client._transport, httpx_socks.AsyncProxyTransport)


def test_legacy_agent_vpn_proxy_alias_still_works():
    """The legacy AGENT_VPN_PROXY env var is accepted as a fallback."""
    httpx_socks = pytest.importorskip("httpx_socks")

    with mock.patch.dict(os.environ, {"AGENT_VPN_PROXY": "socks5h://127.0.0.1:9050"}):
        socks = _reload_socks_module()
        assert socks.is_tor_enabled() is True
        assert socks.get_socks_url() == "socks5h://127.0.0.1:9050"

        client = socks.make_async_client()
        assert isinstance(client._transport, httpx_socks.AsyncProxyTransport)


def test_proxy_url_prefers_jambu_over_legacy():
    """JAMBU_TOR_SOCKS_URL wins when both env vars are set."""
    with mock.patch.dict(
        os.environ,
        {
            "JAMBU_TOR_SOCKS_URL": "socks5h://127.0.0.1:9150",
            "AGENT_VPN_PROXY": "socks5h://127.0.0.1:9050",
        },
    ):
        socks = _reload_socks_module()
        assert socks.get_socks_url() == "socks5h://127.0.0.1:9150"


def test_make_async_client_passes_kwargs_through():
    """Timeout, follow_redirects, and headers are forwarded correctly."""
    socks = _reload_socks_module()
    client = socks.make_async_client(
        timeout=42.0,
        follow_redirects=False,
        headers={"X-Test": "yes"},
    )
    assert client.timeout.connect == 42.0
    assert client.follow_redirects is False
    assert client.headers["X-Test"] == "yes"

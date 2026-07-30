"""Thin Telethon client factory shared by the server and the login helper."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from telethon import TelegramClient
from telethon.sessions import StringSession

from . import config

SOCKS5_SCHEMES = ("socks5", "socks5h")


def _proxy_from_env() -> tuple | None:
    """Build a Telethon SOCKS5 proxy tuple from the environment, if configured.

    Two variables are read, in order of precedence:

    - ``SOCKS5_PROXY`` — ours, so a bare ``host:port`` is accepted as well as a
      full ``socks5://host:port`` URL.
    - ``all_proxy`` / ``ALL_PROXY`` — a general convention shared with curl and
      others, so it may well point at an HTTP or SOCKS4 proxy that Telethon
      cannot use here. Anything that is not a SOCKS5 URL is ignored rather than
      mangled into a broken host.

    Credentials (``socks5://user:pass@host:port``) are passed through.
    """
    raw = os.environ.get("SOCKS5_PROXY")
    if raw:
        # A bare host:port has no scheme for urlsplit to find; assume ours.
        if "://" not in raw:
            raw = f"socks5://{raw}"
    else:
        raw = os.environ.get("all_proxy") or os.environ.get("ALL_PROXY")
    if not raw:
        return None

    parts = urlsplit(raw)
    if parts.scheme not in SOCKS5_SCHEMES or not parts.hostname or not parts.port:
        return None

    # Telethon hands this straight to python_socks' Proxy.create().
    return (
        "socks5",
        parts.hostname,
        parts.port,
        True,  # rdns: resolve hostnames through the proxy
        parts.username,
        parts.password,
    )


def build_client(session: str | None = None) -> TelegramClient:
    """Create a TelegramClient from keyring credentials.

    Pass ``session=""`` to start an empty session (used during login). When
    ``session`` is ``None`` the stored StringSession is loaded from the keyring.

    A SOCKS5 proxy can be supplied via ``SOCKS5_PROXY`` or ``all_proxy`` — see
    :func:`_proxy_from_env`.
    """
    if session is None:
        session = config.get_session()

    return TelegramClient(
        StringSession(session),
        config.get_api_id(),
        config.get_api_hash(),
        # Direct MTProto can be flaky on some networks; retry generously so a
        # dropped connection recovers on its own instead of failing a tool call.
        connection_retries=10,
        retry_delay=2,
        timeout=20,
        proxy=_proxy_from_env(),
    )

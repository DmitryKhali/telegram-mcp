"""Thin Telethon client factory shared by the server and the login helper."""

from __future__ import annotations

import os

from telethon import TelegramClient
from telethon.sessions import StringSession

from . import config


def build_client(session: str | None = None) -> TelegramClient:
    """Create a TelegramClient from keyring credentials.

    Pass ``session=""`` to start an empty session (used during login). When
    ``session`` is ``None`` the stored StringSession is loaded from the keyring.

    Supports SOCKS5 proxy via environment variables:
    - all_proxy=socks5://host:port or SOCKS5_PROXY=host:port
    """
    if session is None:
        session = config.get_session()

    proxy = None
    socks5_url = os.environ.get('all_proxy') or os.environ.get('SOCKS5_PROXY')
    if socks5_url:
        try:
            if socks5_url.startswith('socks5://'):
                socks5_url = socks5_url[9:]
            host, port = socks5_url.rsplit(':', 1)
            proxy = ('socks5', host, int(port))
        except (ValueError, IndexError):
            pass

    return TelegramClient(
        StringSession(session),
        config.get_api_id(),
        config.get_api_hash(),
        # Direct MTProto can be flaky on some networks; retry generously so a
        # dropped connection recovers on its own instead of failing a tool call.
        connection_retries=10,
        retry_delay=2,
        timeout=20,
        proxy=proxy,
    )

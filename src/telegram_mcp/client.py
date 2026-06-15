"""Thin Telethon client factory shared by the server and the login helper."""

from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from . import config


def build_client(session: str | None = None) -> TelegramClient:
    """Create a TelegramClient from keyring credentials.

    Pass ``session=""`` to start an empty session (used during login). When
    ``session`` is ``None`` the stored StringSession is loaded from the keyring.
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
    )

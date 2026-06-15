"""Credential storage backed by the OS keyring (macOS Keychain, etc.).

Nothing secret ever touches the repo or the MCP config file. We read three
items from the keyring under the service name ``telegram-mcp``:

- ``api_id``   — from https://my.telegram.org
- ``api_hash`` — from https://my.telegram.org
- ``session``  — a Telethon StringSession, created by ``telegram-mcp-login``

The service name can be overridden with the ``TELEGRAM_MCP_KEYRING_SERVICE``
env var, which is useful if you want to keep several accounts side by side.
"""

from __future__ import annotations

import os

import keyring

SERVICE = os.environ.get("TELEGRAM_MCP_KEYRING_SERVICE", "telegram-mcp")

API_ID_KEY = "api_id"
API_HASH_KEY = "api_hash"
SESSION_KEY = "session"


class MissingCredential(RuntimeError):
    """Raised when a required credential is not present in the keyring."""


def _get(key: str) -> str | None:
    return keyring.get_password(SERVICE, key)


def _require(key: str, hint: str) -> str:
    value = _get(key)
    if not value:
        raise MissingCredential(
            f"Missing '{key}' in keyring service '{SERVICE}'.\n{hint}"
        )
    return value


def get_api_id() -> int:
    raw = _require(
        API_ID_KEY,
        "Set it with:  keyring set telegram-mcp api_id   (value from https://my.telegram.org)",
    )
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise MissingCredential(f"api_id in keyring is not an integer: {raw!r}") from exc


def get_api_hash() -> str:
    return _require(
        API_HASH_KEY,
        "Set it with:  keyring set telegram-mcp api_hash   (value from https://my.telegram.org)",
    )


def get_session() -> str:
    return _require(
        SESSION_KEY,
        "No login session found. Run:  telegram-mcp-login",
    )


def set_session(session: str) -> None:
    keyring.set_password(SERVICE, SESSION_KEY, session)


def has_session() -> bool:
    return bool(_get(SESSION_KEY))

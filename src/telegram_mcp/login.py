"""One-time interactive login that stores a Telethon StringSession in the keyring.

Run it once:

    telegram-mcp-login

It reads ``api_id`` / ``api_hash`` from the keyring, asks for your phone number
and the login code Telegram sends you (and your 2FA password, if set), then
saves the resulting session string back into the keyring under ``session``.
After that the MCP server can connect without any further interaction.
"""

from __future__ import annotations

import asyncio
import sys

from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from . import config
from .client import build_client


async def _run() -> int:
    try:
        config.get_api_id()
        config.get_api_hash()
    except config.MissingCredential as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    if config.has_session():
        ans = input(
            "A session already exists in the keyring. Overwrite it? [y/N] "
        ).strip().lower()
        if ans not in {"y", "yes"}:
            print("Aborted.")
            return 0

    # Start from an empty session and authorize interactively.
    client = build_client(session="")
    print("\nConnecting to Telegram...")
    await client.connect()

    try:
        print(
            "\nHow do you want to log in?\n"
            "  [1] QR code  — scan with your phone (no SMS/app code needed)\n"
            "  [2] Phone code — Telegram sends a code to your app/SMS"
        )
        method = input("Choose [1/2] (default 1): ").strip() or "1"

        if method == "1":
            ok = await _login_with_qr(client)
        else:
            ok = await _login_with_code(client)
        if not ok:
            return 1

        me = await client.get_me()
        session_str = StringSession.save(client.session)
        config.set_session(session_str)
        name = getattr(me, "first_name", None) or getattr(me, "username", "account")
        print(f"\n✅ Logged in as {name}. Session saved to keyring.")
        print("You can now start the MCP server (telegram-mcp).")
        return 0
    finally:
        await client.disconnect()


async def _sign_in_with_password(client) -> bool:
    """Prompt for the cloud (2FA) password, retrying on a wrong entry."""
    import getpass

    for _ in range(3):
        password = getpass.getpass("Two-step verification password: ")
        try:
            await client.sign_in(password=password)
            return True
        except PasswordHashInvalidError:
            print("Wrong password, try again.", file=sys.stderr)
    print("\n⛔ Too many wrong password attempts. Re-run to try again.", file=sys.stderr)
    return False


def _print_qr(url: str) -> None:
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    # invert=True renders well on the typical light-background terminal.
    qr.print_ascii(invert=True)


async def _login_with_qr(client) -> bool:
    """Log in by displaying a QR code to scan from the Telegram app."""
    qr = await client.qr_login()
    print(
        "\nIn Telegram on your phone: Settings -> Devices -> "
        "Link Desktop Device, then scan this QR code:\n"
    )
    for attempt in range(5):
        _print_qr(qr.url)
        try:
            await qr.wait(timeout=60)
            return True
        except asyncio.TimeoutError:
            print(f"QR expired, generating a new one ({attempt + 1}/5)...\n")
            await qr.recreate()
        except SessionPasswordNeededError:
            return await _sign_in_with_password(client)
    print("\n⛔ QR login timed out. Re-run to try again.", file=sys.stderr)
    return False


async def _login_with_code(client) -> bool:
    """Log in by phone number + the code Telegram sends."""
    raw_phone = input("Phone number (international format, e.g. +1234567890): ")
    # Keep only digits and a single leading '+'; strips stray chars from
    # pasted text, arrow keys, or escape sequences.
    digits = "".join(ch for ch in raw_phone if ch.isdigit())
    phone = ("+" if "+" in raw_phone else "") + digits
    if phone != raw_phone.strip():
        print(f"Using normalized number: {phone}")
    try:
        sent = await client.send_code_request(phone)
    except FloodWaitError as exc:
        mins = exc.seconds // 60
        print(
            f"\n⛔ Telegram is rate-limiting code requests for this number.\n"
            f"   Wait {exc.seconds}s (~{mins} min) before trying again, and "
            f"don't re-run repeatedly in the meantime.",
            file=sys.stderr,
        )
        return False
    except ApiIdInvalidError:
        print(
            "\n⛔ api_id/api_hash are invalid. Re-check the values from "
            "https://my.telegram.org and re-set them:\n"
            "   uv run keyring set telegram-mcp api_id\n"
            "   uv run keyring set telegram-mcp api_hash",
            file=sys.stderr,
        )
        return False
    except PhoneNumberInvalidError:
        print(f"\n⛔ Telegram rejected the phone number: {phone}", file=sys.stderr)
        return False

    sent_type = type(sent.type).__name__.replace("SentCodeType", "")
    next_type = (
        type(sent.next_type).__name__.replace("CodeType", "")
        if sent.next_type
        else None
    )
    print(f"\nTelegram says it sent the code via: {sent_type}", end="")
    if next_type:
        print(f"  (fallback: {next_type})", end="")
    print()
    if sent_type == "App":
        print("→ Look in the Telegram app, in the official 'Telegram' chat.")
    elif sent_type == "Sms":
        print("→ Look for an SMS on your phone.")
    elif sent_type == "Call":
        print("→ You'll receive a phone CALL reading the code aloud.")
    print()
    print(
        "Find the code in the Telegram app (chat 'Telegram', or check "
        "Settings -> Devices to see which device is logged in).\n"
        "Type 'resend' to request a new code if Telegram offers a fallback."
    )

    async def _resend() -> None:
        try:
            resent = await client.send_code_request(phone)
            kind = type(resent.type).__name__.replace("SentCodeType", "")
            print(f"→ New code requested, sent via: {kind}")
        except FloodWaitError as exc:
            print(
                f"⛔ Rate-limited: wait {exc.seconds}s before requesting again.",
                file=sys.stderr,
            )
        except SendCodeUnavailableError:
            print(
                "⛔ Telegram has no more delivery options for this number "
                "right now. The code was already sent to your active "
                "Telegram session — check Settings -> Devices to find it. "
                "Wait a few minutes before requesting again.",
                file=sys.stderr,
            )

    code = ""
    while True:
        entry = input("Login code: ").strip()
        if entry.lower() in {"sms", "call", "resend"}:
            await _resend()
            continue
        if entry:
            code = entry
            break
        print("(empty — type the code, or 'resend' to request a new one)")

    try:
        await client.sign_in(phone=phone, code=code)
    except PhoneCodeInvalidError:
        print("\n⛔ That code is wrong. Re-run and enter it carefully.", file=sys.stderr)
        return False
    except PhoneCodeExpiredError:
        print("\n⛔ That code expired. Re-run to get a fresh one.", file=sys.stderr)
        return False
    except SessionPasswordNeededError:
        return await _sign_in_with_password(client)

    return True


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

"""Telegram MCP server.

Exposes a small set of tools over MCP so any MCP-capable LLM client can read
your Telegram chats and, with explicit confirmation, send messages from your
own account.

Design notes:
- Summaries are NOT produced here. Tools return raw messages; the LLM client
  summarizes them. This keeps the server simple and model-agnostic.
- ``send_message`` never sends without ``confirm=true``; otherwise it returns
  a preview so the user can approve the exact text and recipient first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from telethon import TelegramClient, utils
from telethon.errors import RPCError
from telethon.tl.functions.messages import GetMessageReactionsListRequest
from telethon.tl.types import (
    Channel,
    Chat,
    ReactionCustomEmoji,
    ReactionEmoji,
    User,
)

from . import config
from .client import build_client

INSTRUCTIONS = """\
Tools for reading and sending Telegram messages from the user's own account.

Typical flows:
- "Summarize chat X": call list_dialogs to find the chat, then get_messages,
  then summarize the returned messages yourself.
- "Message person Y": call send_message WITHOUT confirm first to show a preview,
  let the user approve the exact text and recipient, then call again with
  confirm=true.

Never call send_message with confirm=true unless the user has clearly approved
that specific recipient and message text.
"""

mcp = FastMCP("telegram-mcp", instructions=INSTRUCTIONS)

# Single shared client, connected lazily on first tool call.
_client: TelegramClient | None = None


async def get_client() -> TelegramClient:
    global _client
    if _client is None:
        _client = build_client()
    if not _client.is_connected():
        await _client.connect()
    if not await _client.is_user_authorized():
        raise RuntimeError(
            "Telegram session is not authorized. Run 'telegram-mcp-login' first."
        )
    return _client


def _entity_type(entity: Any) -> str:
    if isinstance(entity, User):
        return "bot" if getattr(entity, "bot", False) else "user"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "supergroup"
    if isinstance(entity, Chat):
        return "group"
    return type(entity).__name__


def _entity_name(entity: Any) -> str:
    if isinstance(entity, User):
        parts = [entity.first_name or "", entity.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        if not name:
            name = entity.username or f"user {entity.id}"
        return name
    return getattr(entity, "title", None) or f"chat {getattr(entity, 'id', '?')}"


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _normalize_chat(chat: str | int) -> str | int:
    """Allow numeric strings ('123') to be treated as entity ids."""
    if isinstance(chat, str):
        s = chat.strip()
        if s.lstrip("-").isdigit():
            return int(s)
        return s
    return chat


def _reaction_label(reaction: Any) -> str:
    """Render a reaction as text: the emoji itself, or a tag for custom/paid ones."""
    if isinstance(reaction, ReactionEmoji):
        return reaction.emoticon
    if isinstance(reaction, ReactionCustomEmoji):
        return f"custom_emoji:{reaction.document_id}"
    if type(reaction).__name__ == "ReactionPaid":
        return "paid_star"
    return type(reaction).__name__


async def _peer_name(client: TelegramClient, peer: Any, cache: dict[int, str]) -> tuple[int, str]:
    """Resolve a Peer* to (marked id, display name), memoized in ``cache``."""
    peer_id = utils.get_peer_id(peer)
    if peer_id not in cache:
        try:
            cache[peer_id] = _entity_name(await client.get_entity(peer))
        except (ValueError, RPCError):
            cache[peer_id] = str(peer_id)
    return peer_id, cache[peer_id]


async def _message_reactions(
    client: TelegramClient, msg: Any, cache: dict[int, str]
) -> dict | None:
    """Summarize a message's reactions, or None if it has none.

    ``recent`` (who reacted with what) is only filled where Telegram exposes it:
    private chats and small groups. Channels and big groups give counts only.
    """
    reactions = getattr(msg, "reactions", None)
    if not reactions or not reactions.results:
        return None

    summary: dict = {
        "counts": [
            {
                "reaction": _reaction_label(rc.reaction),
                "count": rc.count,
                "mine": rc.chosen_order is not None,
            }
            for rc in reactions.results
        ],
        "total": sum(rc.count for rc in reactions.results),
    }
    if reactions.recent_reactions:
        recent = []
        for r in reactions.recent_reactions:
            peer_id, name = await _peer_name(client, r.peer_id, cache)
            recent.append(
                {"reaction": _reaction_label(r.reaction), "sender_id": peer_id, "sender": name}
            )
        summary["recent"] = recent
    return summary


@mcp.tool()
async def list_dialogs(limit: int = 50, archived: bool = False) -> list[dict]:
    """List your Telegram chats (dialogs), most recent first.

    Use this to discover chat ids/usernames before reading or sending.

    Args:
        limit: Max number of dialogs to return.
        archived: If true, list archived dialogs instead of the main folder.
    """
    client = await get_client()
    result: list[dict] = []
    async for dialog in client.iter_dialogs(limit=limit, archived=archived):
        entity = dialog.entity
        result.append(
            {
                "id": dialog.id,
                "name": dialog.name or _entity_name(entity),
                "type": _entity_type(entity),
                "username": getattr(entity, "username", None),
                "unread_count": dialog.unread_count,
                "last_message_date": _iso(dialog.date),
            }
        )
    return result


@mcp.tool()
async def get_messages(chat: str | int, limit: int = 50) -> dict:
    """Fetch recent messages from a chat, newest first, for summarizing or review.

    Args:
        chat: Chat id (int), @username, or phone number.
        limit: Max number of messages to fetch.
    """
    client = await get_client()
    entity = await client.get_entity(_normalize_chat(chat))

    sender_cache: dict[int, str] = {}
    messages: list[dict] = []
    async for msg in client.iter_messages(entity, limit=limit):
        sender_name = None
        if msg.sender_id is not None:
            if msg.sender_id in sender_cache:
                sender_name = sender_cache[msg.sender_id]
            else:
                sender = await msg.get_sender()
                sender_name = _entity_name(sender) if sender else str(msg.sender_id)
                sender_cache[msg.sender_id] = sender_name
        item = {
            "id": msg.id,
            "date": _iso(msg.date),
            "sender_id": msg.sender_id,
            "sender": sender_name,
            "outgoing": bool(msg.out),
            "text": msg.message or "",
            "media": type(msg.media).__name__ if msg.media else None,
        }
        reactions = await _message_reactions(client, msg, sender_cache)
        if reactions:
            item["reactions"] = reactions
        messages.append(item)

    return {
        "chat": {"id": entity.id, "name": _entity_name(entity), "type": _entity_type(entity)},
        "count": len(messages),
        "messages": messages,
    }


@mcp.tool()
async def search_messages(query: str, chat: str | int | None = None, limit: int = 50) -> dict:
    """Search messages by text, either globally or within a single chat.

    Args:
        query: Text to search for.
        chat: Optional chat id/@username/phone to limit the search to.
        limit: Max number of matching messages to return.
    """
    client = await get_client()
    entity = await client.get_entity(_normalize_chat(chat)) if chat is not None else None

    name_cache: dict[int, str] = {}
    results: list[dict] = []
    async for msg in client.iter_messages(entity, search=query, limit=limit):
        item = {
            "id": msg.id,
            "chat_id": msg.chat_id,
            "date": _iso(msg.date),
            "sender_id": msg.sender_id,
            "outgoing": bool(msg.out),
            "text": msg.message or "",
        }
        reactions = await _message_reactions(client, msg, name_cache)
        if reactions:
            item["reactions"] = reactions
        results.append(item)
    return {"query": query, "count": len(results), "messages": results}


@mcp.tool()
async def get_message_reactions(chat: str | int, message_id: int, limit: int = 100) -> dict:
    """List who reacted to a message and with what.

    get_messages already includes reaction counts (and recent reactors where
    available); use this for the full per-person list on a specific message.
    Telegram only allows this in private chats and groups where the reactor list
    is visible — not in channels.

    Args:
        chat: Chat id (int), @username, or phone number.
        message_id: Id of the message (from get_messages / search_messages).
        limit: Max number of individual reactions to return.
    """
    client = await get_client()
    entity = await client.get_entity(_normalize_chat(chat))

    msg = await client.get_messages(entity, ids=message_id)
    if msg is None:
        raise ValueError(f"Message {message_id} not found in this chat")

    name_cache: dict[int, str] = {}
    summary = await _message_reactions(client, msg, name_cache)
    result: dict = {
        "chat_id": entity.id,
        "message_id": message_id,
        "counts": summary["counts"] if summary else [],
        "total": summary["total"] if summary else 0,
        "reactors": [],
    }
    if not summary:
        return result

    reactors: list[dict] = []
    offset: str | None = None
    try:
        while len(reactors) < limit:
            page = await client(
                GetMessageReactionsListRequest(
                    peer=entity,
                    id=message_id,
                    limit=min(100, limit - len(reactors)),
                    offset=offset,
                )
            )
            # Prime the cache from the response so names resolve without extra calls.
            for ent in [*page.users, *page.chats]:
                name_cache[utils.get_peer_id(ent)] = _entity_name(ent)
            for r in page.reactions:
                peer_id, name = await _peer_name(client, r.peer_id, name_cache)
                reactors.append(
                    {
                        "reaction": _reaction_label(r.reaction),
                        "sender_id": peer_id,
                        "sender": name,
                        "date": _iso(r.date),
                    }
                )
            offset = page.next_offset
            if not offset or not page.reactions:
                break
    except RPCError as exc:
        result["reactors_unavailable"] = (
            f"Telegram does not expose the reactor list here ({exc.__class__.__name__})"
        )
        result["reactors"] = summary.get("recent", [])
        return result

    result["reactors"] = reactors[:limit]
    return result


@mcp.tool()
async def get_chat_info(chat: str | int) -> dict:
    """Get metadata about a chat or user (id, name, type, username).

    Args:
        chat: Chat id (int), @username, or phone number.
    """
    client = await get_client()
    entity = await client.get_entity(_normalize_chat(chat))
    info = {
        "id": entity.id,
        "name": _entity_name(entity),
        "type": _entity_type(entity),
        "username": getattr(entity, "username", None),
    }
    if isinstance(entity, User):
        info["phone"] = entity.phone
        info["is_contact"] = bool(getattr(entity, "contact", False))
    return info


@mcp.tool()
async def send_message(chat: str | int, text: str, confirm: bool = False) -> dict:
    """Send a message to a chat or user. Requires explicit confirmation.

    SAFETY: With confirm=false (default) this does NOT send — it resolves the
    recipient and returns a preview. Only call again with confirm=true after the
    user has approved the exact recipient and text.

    Args:
        chat: Chat id (int), @username, or phone number.
        text: Message text to send.
        confirm: Must be true to actually send.
    """
    client = await get_client()
    entity = await client.get_entity(_normalize_chat(chat))
    recipient = {
        "id": entity.id,
        "name": _entity_name(entity),
        "type": _entity_type(entity),
        "username": getattr(entity, "username", None),
    }

    if not confirm:
        return {
            "sent": False,
            "preview": True,
            "recipient": recipient,
            "text": text,
            "note": "Not sent. Re-call with confirm=true to send this message.",
        }

    sent = await client.send_message(entity, text)
    return {
        "sent": True,
        "recipient": recipient,
        "message_id": sent.id,
        "date": _iso(sent.date),
        "text": text,
    }


@mcp.tool()
async def send_file(
    chat: str | int,
    file_path: str,
    caption: str = "",
    confirm: bool = False,
) -> dict:
    """Send a local file (document/photo/video) to a chat or user.

    SAFETY: With confirm=false (default) this does NOT send — it resolves the
    recipient and returns a preview. Only call again with confirm=true after the
    user has approved the exact recipient and file.

    Args:
        chat: Chat id (int), @username, or phone number.
        file_path: Absolute path to the local file to send.
        caption: Optional caption text to attach to the file.
        confirm: Must be true to actually send.
    """
    import os

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"No such file: {file_path}")

    client = await get_client()
    entity = await client.get_entity(_normalize_chat(chat))
    recipient = {
        "id": entity.id,
        "name": _entity_name(entity),
        "type": _entity_type(entity),
        "username": getattr(entity, "username", None),
    }

    if not confirm:
        return {
            "sent": False,
            "preview": True,
            "recipient": recipient,
            "file_path": file_path,
            "caption": caption,
            "note": "Not sent. Re-call with confirm=true to send this file.",
        }

    sent = await client.send_file(entity, file_path, caption=caption or None)
    return {
        "sent": True,
        "recipient": recipient,
        "message_id": sent.id,
        "date": _iso(sent.date),
        "file_path": file_path,
        "caption": caption,
    }


def main() -> None:
    # Fail fast with a clear message if credentials are missing.
    try:
        config.get_api_id()
        config.get_api_hash()
    except config.MissingCredential as exc:
        raise SystemExit(f"\n{exc}\n")
    mcp.run()


if __name__ == "__main__":
    main()

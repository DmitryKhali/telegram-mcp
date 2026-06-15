# telegram-mcp

An [MCP](https://modelcontextprotocol.io) server for Telegram. It lets an LLM
client (Claude Desktop / Claude Code) read your Telegram chats, summarize them,
and — with explicit confirmation — send messages **from your own account**.

It talks to Telegram over the **MTProto user API** via
[Telethon](https://docs.telethon.dev), so it can access your real dialogs and
direct messages. (A Bot-API bot cannot read your personal chats or message
arbitrary people — that's why this uses a user session, not a bot token.)

> Replace `YOUR_GITHUB_USERNAME` in `pyproject.toml` and in the clone command
> below with your actual GitHub handle before publishing.

## Features

| Tool | What it does |
|------|--------------|
| `list_dialogs` | List your chats (and archived chats) to find one by name/id |
| `get_messages` | Fetch recent messages from a chat (for summarizing/review) |
| `search_messages` | Search messages globally or within one chat |
| `get_chat_info` | Metadata about a chat or user |
| `send_message` | Send a message — **requires `confirm=true`**, returns a preview otherwise |

Summaries are produced by the LLM client, **not** the server — the server only
returns raw messages.

## Security

- Credentials (`api_id`, `api_hash`, and your login session) live in your OS
  keyring (macOS Keychain, GNOME Keyring, Windows Credential Locker) via
  [`keyring`](https://pypi.org/project/keyring/). They are **never** written to
  the repo, an `.env`, or the MCP config file.
- The login session grants full access to your account — treat it like a
  password. Anyone with it can act as you on Telegram.
- `send_message` will not send anything without `confirm=true`, so the model
  always shows you a preview first.

---

## Setup

### 0. Prerequisites

- Python **3.10+**
- [`uv`](https://docs.astral.sh/uv/) (recommended). Install it with:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 1. Clone and install

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/telegram-mcp
cd telegram-mcp
uv sync
```

### 2. Get Telegram API credentials

1. Go to https://my.telegram.org and log in with your phone number.
2. Open **API development tools**, create an app (any title, e.g.
   `telegram-mcp`).
3. Copy the **api_id** (a number) and **api_hash** (a 32-char hex string).

### 3. Store credentials in the keyring

```bash
uv run keyring set telegram-mcp api_id      # paste the numeric api_id
uv run keyring set telegram-mcp api_hash    # paste the api_hash
```

Each command prompts for the value and **does not echo it** — that's expected.

### 4. Log in (one time)

```bash
uv run telegram-mcp-login
```

You'll be asked to choose a login method:

- **[1] QR code** (recommended) — a QR code is printed in the terminal. On your
  phone open Telegram → **Settings → Devices → Link Desktop Device** and scan
  it. No SMS/app code needed.
- **[2] Phone code** — enter your phone number; Telegram sends a code.

If you have two-step verification, you'll be prompted for that cloud password
too (you get a few attempts). On success the session is saved to the keyring
and you're done — the server connects automatically from now on.

### 5. Connect to Claude

**Claude Desktop** — edit the config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add a `telegram` entry under `mcpServers` (keep any existing servers):

```json
{
  "mcpServers": {
    "telegram": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/telegram-mcp", "run", "telegram-mcp"]
    }
  }
}
```

> Claude Desktop launches with a minimal `PATH` and may not find `uv`. If the
> server fails to start, use the **absolute path** to `uv` (find it with
> `which uv`, e.g. `/Users/you/.local/bin/uv`) as `"command"`.

Restart Claude Desktop afterwards.

**Claude Code**:

```bash
claude mcp add telegram -s user -- uv --directory /absolute/path/to/telegram-mcp run telegram-mcp
```

Check it connected with `claude mcp list` (look for `telegram: ... ✓ Connected`).

---

## Usage

Ask Claude things like:

- "Summarize my chat with Alice from the last day."
- "What's new in the 'Team' group?" (it can read archived chats too)
- "Draft a message to @bob saying I'll be 10 minutes late." — Claude shows a
  preview; you confirm before it sends.

---

## Troubleshooting

These cover the gotchas you're most likely to hit during first login.

### The login code never arrives

Telegram usually sends the login code as a **message inside the Telegram app**
(the official "Telegram" chat), **not** as an SMS — especially if you're already
logged in on another device. The login script prints where Telegram says it
sent the code (`App` / `Sms` / `Call`).

- If it says **App**: open Telegram and check the "Telegram" service chat. Use
  **Settings → Devices** to see *which* device is logged in — the code is in
  that device's "Telegram" chat (often it's the desktop app, not the phone).
- Some numbers get **no SMS fallback** at all — Telegram only offers the `App`
  code. In that case you must read it from an active Telegram session, or use
  **QR login** instead (method 1), which sidesteps codes entirely.
- Requesting the code many times in a row triggers a **flood wait** (temporary
  block). The script reports this — wait it out, don't keep retrying.

**Easiest fix: use QR login.** It avoids the code-delivery problem completely.

### The phone number gets mangled / rejected

If you use the arrow keys or paste while typing the number, stray characters can
sneak in. The script strips everything except digits and a leading `+`, and
prints the normalized number — make sure it's correct before continuing.

### Connection times out ("Attempt N at connecting failed: TimeoutError")

Some networks (or ISP-level filtering, common in certain regions) throttle
direct MTProto connections, so login and tool calls hang or only succeed after
many retries. Fixes:

- **Enable a VPN** so the traffic is tunneled, then retry. This is the simplest
  reliable fix.
- The client already retries connecting several times, so a flaky connection
  often gets through eventually.
- For a built-in proxy instead of a system VPN, Telethon supports SOCKS5 and
  MTProxy via the `proxy=` argument in `build_client()` (see
  `src/telegram_mcp/client.py`).

### "Telegram session is not authorized"

The session is missing or was invalidated. Re-run `uv run telegram-mcp-login`.

### Switching accounts / starting over

Delete the stored items and log in again:

```bash
uv run keyring del telegram-mcp session
uv run keyring del telegram-mcp api_id
uv run keyring del telegram-mcp api_hash
```

To run **multiple accounts** side by side, set a different keyring namespace per
account via the `TELEGRAM_MCP_KEYRING_SERVICE` env var (default: `telegram-mcp`)
and store credentials under that name.

---

## Notes & limits

- **Don't mass-message.** Sending to many new people, or sending too fast, can
  get your account limited or banned by Telegram's anti-spam. This tool is for
  personal assistant-style use, not bulk outreach.
- This is an unofficial tool and not affiliated with Telegram.

## License

[MIT](LICENSE)

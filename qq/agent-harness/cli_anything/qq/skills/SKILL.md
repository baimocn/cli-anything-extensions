---
name: "cli-anything-qq"
description: "Drive QQ from the terminal via OneBot 11 HTTP API (SnowLuma/NapCat) — contacts, chat history reading, sending, and a self-verifying roundtrip. Use when an agent needs to replace the QQ GUI: read conversations, summarize, and reply through structured commands."
---

# cli-anything-qq

Use this skill to operate QQ entirely from the CLI through the **OneBot 11
HTTP action API** (backed by SnowLuma / NapCat bridging the real QQ client).
Pure HTTP: no daemon, no WebSocket required for any command.

## Requirements

- Python 3.10+
- A running OneBot v11 HTTP endpoint (SnowLuma default: `http://127.0.0.1:3000`)
- Access token if the endpoint requires auth (`QQ_NAPCAT_TOKEN` / `--token`)

## Install

```bash
cd qq/agent-harness
pip install -e .
```

Configure via env (flags `--url` / `--token` override per command):

```bash
export QQ_NAPCAT_URL=http://127.0.0.1:3000
export QQ_NAPCAT_TOKEN=<token>
```

## Usage

```bash
cli-anything-qq --json status                          # connectivity + identity
cli-anything-qq --json friend list                     # contacts
cli-anything-qq --json group list                      # groups
cli-anything-qq --json group members <group_id>        # group members
cli-anything-qq msg history private <user_id>          # read chat (human-readable)
cli-anything-qq --json msg history group <group_id> -n 40
cli-anything-qq --json send private 123456 "hi"        # send (returns message_id)
cli-anything-qq --json msg get 12345                   # read back by id
cli-anything-qq --json roundtrip private 123456 "ping" # send + verify delivery
```

## JSON output contract

Every command emits `{"ok": bool, "command": str, "data": ..., "error": null | {type, message}}`.
Failures set `ok=false` and exit code 1. Stable command ids include `status`,
`friend.list`, `group.list`, `group.members`, `send.private`, `send.group`,
`msg.get`, `msg.history`, `roundtrip`, `project.*`, `session.*`.

## Command groups

- `status` — connectivity + logged-in identity (`get_login_info`)
- `friend` — `list`
- `group` — `list`, `members <group_id>`
- `send` — `private <user_id> <message>`, `group <group_id> <message>`
- `msg` — `get <message_id>`, `history private|group <id> [-n N]`
- `roundtrip <private|group> <id> <message>` — send then read back
- `project` / `session` — local state, undo/redo

## Agent workflow: replacing the QQ GUI

An agent can fully operate the account without the QQ app UI:

1. **Discover peers**: `friend list` / `group list` (JSON ids + nicknames).
2. **Read a conversation**: `msg history private <user_id>` or
   `msg history group <group_id>` — oldest-first lines
   `[MM-DD HH:MM] sender(id): text`. Non-text segments render compactly
   (`[图片]`, `[语音]`, `@qq`, `[回复:id]`, `[卡片]`) so rich chats stay readable.
3. **Summarize & decide**, then reply: `send private <id> "<reply>"` or
   `send group <id> "<reply>"`. Use `roundtrip` when delivery must be proven.
4. **Poll for new activity**: re-run `msg history` and diff on `message_id` /
   timestamps; the peer's replies appear as new entries.

This read → summarize → reply loop is the intended replacement for the GUI.

## Agent guidance

- Prefer `--json` for deterministic parsing.
- `message` accepts a plain string or OneBot message segments (CQ passthrough).
- Never paste a real access token into source or shared state files.
- Unofficial personal-account protocol: account risk is the operator's own.

## Test workflow

```bash
cd qq/agent-harness
pip install -e .
pytest cli_anything/qq/tests/test_core.py -v   # 26 tests, no real QQ needed
```

Core tests fully mock the endpoint and include the four ablation experiments
(token / endpoint / retcode / envelope) proving each mechanism necessary.
See `cli_anything/qq/tests/TEST.md`.

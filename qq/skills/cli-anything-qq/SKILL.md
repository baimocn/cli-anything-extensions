---
name: "cli-anything-qq"
description: "Drive QQ from the terminal via OneBot 11 HTTP API (SnowLuma/NapCat) — contacts, chat history reading, sending, revoking, reacting, poking, group admin, file upload, OCR, and a self-verifying roundtrip. Use when an agent needs to replace the QQ GUI: read conversations, summarize, and reply through structured commands."
---

# cli-anything-qq

Operate QQ entirely from the CLI through the **OneBot 11 HTTP action API**
(bridged to the real QQ client by SnowLuma / NapCat). Pure HTTP: no daemon and
no WebSocket needed for any command.

## Requirements

- Python 3.10+
- A running OneBot v11 HTTP endpoint (SnowLuma default: `http://127.0.0.1:3000`)
- Access token if the endpoint requires auth (`QQ_NAPCAT_TOKEN` / `--token`)

## Install

```bash
pip install git+https://github.com/baimocn/cli-anything-extensions.git#subdirectory=qq/agent-harness
# or, from a checkout:
cd qq/agent-harness && pip install -e .
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
Failures set `ok=false` and exit code 1. Stable command ids: `status`,
`friend.list`, `group.list`, `group.members`, `group.info`, `send.private`,
`send.group`, `msg.get`, `msg.history`, `msg.revoke`, `msg.mark-read`,
`msg.like`, `msg.ocr`, `poke.private`, `poke.group`, `group.ban`,
`group.whole-ban`, `group.kick`, `group.card`, `group.admin`, `group.title`,
`group.leave`, `group.essence`, `group.at-all-remain`, `group.sign`,
`upload.private`, `upload.group`, `profile.set`, `request.friend`,
`request.group`, `call.<action>`, `roundtrip`, `project.*`, `session.*`.

## Command reference

| Group | Commands |
|---|---|
| `status` | connectivity + logged-in identity (`get_login_info`) |
| `friend` | `list` |
| `group` | `list`, `members`, `info`, `essence`, `at-all-remain`, `sign`, `ban`, `whole-ban`, `kick`, `card`, `admin`, `title`, `leave` (confirmation) |
| `send` | `private <user_id> <message>`, `group <group_id> <message>` |
| `msg` | `get`, `history private\|group [-n N]`, `revoke`, `mark-read`, `like <id> [emoji_id] [--remove]`, `ocr <image>` |
| `poke` | `private <user_id>`, `group <group_id> <user_id>` |
| `upload` | `private <user_id> <file>`, `group <group_id> <file>` |
| `request` | `friend <flag> [--deny --remark]`, `group <flag> [--deny]` |
| `profile-set` | `--nickname / --signature` |
| `call` | `<action> --params '{...}'` — raw passthrough to the full OneBot surface |
| `roundtrip` | `<private\|group> <id> <message>` — send then read back |
| `project` / `session` | local state, undo/redo |

Message text accepts a plain string or OneBot message segments (CQ passthrough).

## Agent workflow: replacing the QQ GUI

1. **Discover peers**: `friend list` / `group list`.
2. **Read a conversation**: `msg history private <user_id>` or
   `msg history group <group_id>` — oldest-first lines
   `[MM-DD HH:MM] sender(id): text`; non-text segments render compactly
   (`[图片]`, `[语音]`, `@qq`, `[回复:id]`, `[卡片]`).
3. **Summarize & decide**, then reply via `send` (use `roundtrip` to prove
   delivery, `msg revoke` to recall a just-sent mistake).
4. **Poll for new activity**: re-run `msg history`, diff on `message_id` /
   timestamps.

## Agent guidance

- Prefer `--json` for deterministic parsing.
- Never paste a real access token into source or shared state files.
- Unofficial personal-account protocol: account risk is the operator's own.

## Known backend limitations (SnowLuma v2.4.2)

- `set_msg_emoji_like` exists but fails at runtime (retcode=100) — backend
  limitation; the CLI side is correct.
- `translate`, `send_voice`, `send_music`, `get_qq_status`,
  `get_robot_uin_range` unsupported; send voice as a `[record]` segment.

## Test workflow

```bash
cd qq/agent-harness && pip install -e . && pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest cli_anything/qq/tests/test_core.py -v
# 38 tests, fully mocked OneBot — no real QQ account needed
```

Includes four ablation experiments (token / endpoint / retcode / envelope).
See `cli_anything/qq/tests/TEST.md`.

# cli-anything-qq

A stateful CLI harness for QQ, backed by the **OneBot 11 HTTP action API**
(as exposed by NapCat / Lagrange). It lets AI agents drive QQ through
structured commands and a stable `--json` output contract.

**Status:** minimal closed-loop v1 — connectivity, send, read-back, and a
combined `roundtrip` command. WebSocket event subscription and friend/group
management are intentionally out of scope for v1.

## Requirements

- Python 3.10+
- A running NapCat (or other OneBot 11 implementation) with its HTTP endpoint
  enabled, e.g. `http://127.0.0.1:3000`
- Optional access token if the endpoint requires authentication

## Install

```bash
cd qq/agent-harness
pip install -e .
```

## Configure

```bash
export QQ_NAPCAT_URL=http://127.0.0.1:3000
export QQ_NAPCAT_TOKEN=<your access token>
```

Flags `--url` / `--token` override the environment for a single command.

## Usage

```bash
# Interactive REPL (default, no subcommand)
cli-anything-qq

# Connectivity + identity
cli-anything-qq --json status

# Send a private message / group message
cli-anything-qq --json send private 123456 "hello"
cli-anything-qq --json send group 654321 "hello group"

# Read a message back by id
cli-anything-qq --json msg get 12345

# The full closed loop in one command
cli-anything-qq --json roundtrip private 123456 "ping"
```

## JSON output contract

Every command emits (with `--json`):

```json
{"ok": true, "command": "send.private", "data": {...}, "error": null}
```

On failure: `ok=false`, `error={type, message}`, exit code `1`.

## Compliance notice

The OneBot/NapCat personal-account protocol is not an official QQ API. Using
it may violate QQ's terms of service and may put the account at risk. This
tool is a local CLI wrapper only and contains no detection-evasion logic.
Use it at your own risk.

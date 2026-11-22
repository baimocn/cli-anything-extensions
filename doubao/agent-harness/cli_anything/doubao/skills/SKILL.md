---
name: "cli-anything-doubao"
description: "Drive the Doubao (豆包) desktop app from the terminal via the Chrome DevTools Protocol — conversations, prompts, finished answers, transcripts, screenshots. Fully background: typing, clicking and reading all happen as in-page CDP events, no OS focus needed. Use when an agent needs to replace the Doubao GUI: ask questions, read AI answers, manage the conversation list."
---

# cli-anything-doubao

Operate the **Doubao desktop app** (豆包, ByteDance's AI assistant) entirely
from the CLI through the **Chrome DevTools Protocol**. The Windows app is a
Chromium 147 shell; launched with `--remote-debugging-port` it exposes the
standard CDP, and every command (typing, clicking, reading, screenshots) runs
as an in-page event — **fully background**, exactly like the QQ OneBot channel.

## Requirements

- Windows 10/11, Doubao desktop app (豆包桌面版, verified on 2.37.2 / Chromium 147)
- Python 3.10+
- The app running with `--remote-debugging-port=9333` (or auto-started via
  `--launch` / `DOUBAO_APP_PATH`)

## Install

```bash
pip install git+https://github.com/baimocn/cli-anything-extensions.git#subdirectory=doubao/agent-harness
```

Configure via env (flags `--port` / `--app-path` override):

```bash
export DOUBAO_CDP_PORT=9333          # debug port (probed, never hijacked)
export DOUBAO_APP_PATH="D:/豆包/Doubao/Doubao.exe"  # for auto-start
```

## Usage

```bash
cli-anything-doubao --json status                 # endpoint + page + editor health
cli-anything-doubao --json status --launch        # + auto-start the app
cli-anything-doubao --json chat list              # sidebar conversations
cli-anything-doubao --json chat new               # fresh conversation
cli-anything-doubao --json chat open 1            # open sidebar entry #1
cli-anything-doubao ask "1+1等于几？只回答数字"     # send + wait for finished answer
cli-anything-doubao --json read                   # latest answer
cli-anything-doubao --json history -n 20          # transcript (oldest-first)
cli-anything-doubao --json screenshot -o shot.png # background page capture
cli-anything-doubao --json eval "document.title"  # one-off page JS
```

## JSON output contract

Every command emits `{"ok": bool, "command": str, "data": ..., "error": null | {type, message}}`.
Failures set `ok=false` and exit code 1. Stable command ids: `status`,
`chat.list`, `chat.new`, `chat.open`, `ask`, `read`, `history`, `screenshot`,
`eval`, `project.*`, `state.*`.

## Agent workflow: replacing the Doubao GUI

1. **Health check**: `status` — `cdp_alive` and `page.found` must both be OK
   (`--launch` auto-starts the app if the endpoint is down).
2. **Conversations**: `chat list` (sidebar entries), `chat new` (fresh chat),
   `chat open <n>` (switch).
3. **Ask**: `ask "<prompt>"` — sends the prompt and waits until the answer
   stops streaming, then returns the finished text
   (`round_trip_ok: true`). A timeout still returns the partial answer.
4. **Read**: `read` (latest answer), `history` (ordered transcript with
   `role: user|assistant`).
5. **See**: `screenshot -o out.png` — page capture without touching the window.

## How it works (agent-relevant internals)

- **Control surface**: CDP over `http://127.0.0.1:{port}` (page discovery via
  `/json/list`, commands via a per-page WebSocket with the `Origin` header
  suppressed — new Chromium builds reject it otherwise; verified live).
- **The DOM contract** (verified on Doubao 2.37.2): editor =
  `.tiptap.ProseMirror` (TipTap), send button =
  `[data-testid="chat_input_send_button"]`, messages =
  `[data-testid="send_message"]` / `[data-testid="receive_message"]` with
  `[data-testid="message_text_content"]` bodies, sidebar entries =
  `[data-testid="conversation-list-v2-item"]`, new chat =
  `[data-testid="create_conversation_button"]`.
- **Completion detection**: `ask` polls the chat DOM until the
  `receive_message` count grows past the pre-send baseline AND the answer's
  length holds stable for two consecutive polls (streaming text grows; a
  finished answer does not).
- **Fully background**: `Input.insertText` and DOM clicks are page-level
  events; the OS window never needs focus. Reads and writes are equally
  invisible — unlike WeChat 4.x, no accessibility-gate or focus borrowing.
- **Port safety**: auto-launch probes the port first and refuses to hijack a
  foreign listener — set `DOUBAO_CDP_PORT` to a free port instead.

## Agent guidance

- Prefer `--json` for deterministic parsing.
- The account is your own Doubao login; prompts go to the AI chat only.
- Keep real account names out of issue reports and shared state files.

## Known limitations

- The app must run with the debug flag; after a Doubao self-update the flag
  is lost (relaunch with `--launch` or a desktop shortcut that includes it).
- Only the chat surface is wrapped (prompts/answers/conversations). 定时任务,
  云盘, 工作台 pages exist as separate CDP targets and are reachable via
  `eval` if needed.

## Test workflow

```bash
cd doubao/agent-harness && pip install -e . && pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
# 39 tests, fully mocked CDP — no Doubao, no network
```

Includes four ablation experiments; see `cli_anything/doubao/tests/TEST.md`.
Real-machine E2E (opt-in): `DOUBAO_E2E=1 pytest cli_anything/doubao/tests/test_full_e2e.py`.

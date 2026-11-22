# DOUBAO.md — SOP: the Doubao control surface

## Why CDP

| Doubao surface | Status |
|---|---|
| Chat UI | a Chromium 147 shell page (`doubao://doubao-chat/chat`), Tailwind + TipTap/ProseMirror editor, `data-testid` attributes everywhere |
| Automation protocol | standard CDP via `--remote-debugging-port` — first-class, no injection, no UIA |
| Focus requirement | **none** — CDP Input events are page-level; the OS window can stay in the tray |

The QQ harness needs a third-party bridge (SnowLuma); WeChat 4.x has no free
protocol at all (UIA + focus borrowing). Doubao ships its own automation
surface — one launch flag — making it the cleanest of the three.

## DOM contract (verified live, Doubao 2.37.2 / Chromium 147)

| Element | Selector |
|---|---|
| Prompt editor | `.tiptap.ProseMirror` |
| Send button | `[data-testid="chat_input_send_button"]` |
| User message | `[data-testid="send_message"]` |
| Assistant message | `[data-testid="receive_message"]` |
| Message text | `[data-testid="message_text_content"]` |
| Sidebar conversations | `[data-testid="conversation-list-v2-item"]` |
| New conversation | `[data-testid="create_conversation_button"]` |

## Closed loop (ask → wait → read)

```bash
cli-anything-doubao --json status --launch     # 1. endpoint up (auto-start)
cli-anything-doubao --json chat list           # 2. conversations
cli-anything-doubao ask "你的问题"               # 3. send + wait for completion
cli-anything-doubao --json history             # 4. full transcript
```

Completion = `receive_message` count grew past baseline AND answer length
stable for 2 consecutive polls (1.2 s apart).

## Gotchas

- **Origin 403**: Chromium ≥ 111 rejects CDP WebSocket upgrades carrying an
  `Origin` header. The backend suppresses it (`suppress_origin=True`) — if
  you write your own client, do the same.
- **Debug flag resets on update**: Doubao self-updates drop the launch flag;
  relaunch with `--launch` (auto-probes a free port, never hijacks).
- **Login**: first launch may require phone QR login — do it once in the GUI.

## Verified live (2026-11-22)

- CDP `/json/version` → `Chrome/147.0.7727.149`
- Background screenshot via `Page.captureScreenshot` (window untouched)
- Send "1+1等于几？只回答数字" → answer "2" extracted from
  `receive_message` — full background roundtrip

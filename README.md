# cli-anything-extensions

**Agent-native CLI extensions** — a collection of stateful CLI harnesses that
let AI agents operate desktop software designed for humans.

Built with the [CLI-Anything](https://github.com/HKUDS/CLI-Anything) methodology
(HARNESS SOP): find the headless control surface of a GUI app, wrap it in a
stateful CLI, expose a stable `--json` contract. Each subdirectory is one app.

## Apps in this repo

| App | Status | Control surface | What the agent can do |
|---|---|---|---|
| **QQ** (`qq/`) | ✅ v1.0 — verified end-to-end on a real account | OneBot 11 HTTP (SnowLuma / NapCat bridge) | Read chats, summarize & reply, contacts, group admin, revoke/react/poke, file upload, OCR, profile |
| **WeChat** (`wx/`) | ✅ v1.0 — verified end-to-end on WeChat 4.2.0.13 | decrypted local DBs (read) + UIA driving via wechatauto (send) | Read chats/contacts/groups/history from decrypted SQLCipher DBs, send/recall/poke via the real client, self-verifying roundtrip |
| **Doubao** (`doubao/`) | ✅ v1.0 — verified end-to-end on Doubao 2.37.2 | Chrome DevTools Protocol (the app's own Chromium 147 shell) | Ask the AI and wait for finished answers, conversation list/switch/new, transcripts, background screenshots |

## Doubao — replace the Doubao GUI with a CLI

The Doubao Windows app is a Chromium shell, so one launch flag
(`--remote-debugging-port`) exposes a first-class automation protocol. Every
command runs as an in-page CDP event — typing, clicking, reading and
screenshots are **fully background**, no OS focus involved (like QQ; unlike
WeChat, whose send path must borrow the window).

```bash
pip install git+https://github.com/baimocn/cli-anything-extensions.git#subdirectory=doubao/agent-harness

cli-anything-doubao --json status --launch          # endpoint up (auto-starts)
cli-anything-doubao ask "1+1等于几？只回答数字"       # send + wait for the answer
cli-anything-doubao --json history                  # ordered transcript
```

Full reference: [`doubao/skills/cli-anything-doubao/SKILL.md`](doubao/skills/cli-anything-doubao/SKILL.md).

## WeChat — replace the WeChat 4.x GUI with a CLI

WeChat PC 4.x has no protocol bridge, so this harness splits the job in two:
**reads** come from decrypted local SQLCipher databases (fully background,
works even with the client hidden in the tray) and **sends** drive the real
client via UIA (wechatauto's hybrid driver), inside a window session that
restores the client, borrows the focus for a few seconds, and hands both
focus and z-order straight back. Delivery is proven by reading the message
back out of the decrypted DB.

```bash
pip install git+https://github.com/baimocn/cli-anything-extensions.git#subdirectory=wx/agent-harness
pip install "cli-anything-wx[real]"   # Windows + WeChat 4.x client

cli-anything-wx --json status                        # both channels' health
cli-anything-wx --json session list                  # the session panel
cli-anything-wx msg history 文件传输助手               # read (oldest-first)
cli-anything-wx --json roundtrip 文件传输助手 "hi"     # send + DB read-back proof
```

Full reference: [`wx/skills/cli-anything-wx/SKILL.md`](wx/skills/cli-anything-wx/SKILL.md).
Research notes on background-send routes (paid hooks, iPad protocols, why
"fake background" projects don't hold up): [`wx/agent-harness/WX.md`](wx/agent-harness/WX.md).

## QQ — replace the QQ GUI with a CLI

The QQ harness is a pure-HTTP OneBot 11 client: no daemon, no WebSocket needed
for any command, 39/44 OneBot actions reachable (probe-verified against
SnowLuma v2.4.2).

### Install

```bash
pip install git+https://github.com/baimocn/cli-anything-extensions.git#subdirectory=qq/agent-harness
```

Requirements: Python 3.10+, a running OneBot v11 endpoint. The reference setup
is [SnowLuma](https://github.com/TomyJan/SnowLuma) (NapCat successor) bridging
a real, logged-in QQ client — it exposes `http://127.0.0.1:3000` and needs no
QR scan and no external sign server.

### Configure

```bash
export QQ_NAPCAT_URL=http://127.0.0.1:3000
export QQ_NAPCAT_TOKEN=<access token from your OneBot config>
```

### The agent loop (read → summarize → reply)

```bash
cli-anything-qq --json friend list                 # discover peers
cli-anything-qq msg history private <user_id>      # read a conversation
#   [11-22 08:40] nickname(id): text    (images/@/replies render compactly)
cli-anything-qq --json send private <id> "reply"   # send
cli-anything-qq --json roundtrip private <id> "x"  # send + verify delivery
```

Beyond messaging: `msg revoke|mark-read|like|ocr`, `poke private|group`,
`group ban|kick|card|admin|title|info|essence|sign|at-all-remain`,
`upload private|group`, `profile-set`, `request friend|group`, and `call <action>`
as a raw passthrough for the entire OneBot surface.

Full command reference: [`qq/skills/cli-anything-qq/SKILL.md`](qq/skills/cli-anything-qq/SKILL.md)
(the file agents are meant to read).

### Verify

```bash
cd qq/agent-harness && pip install -e . && pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest cli_anything/qq/tests/test_core.py -v
# 38 tests, fully mocked OneBot — no real QQ account needed
```

The test suite includes four **ablation experiments** proving each mechanism
(access token, endpoint, retcode handling, response envelope) is necessary.

## Design notes

- **Pure HTTP closed loop**: OneBot's natural "receive" path is a WebSocket
  event stream, which would need a resident daemon. v1–v3 instead cover
  send, read-back (`get_msg`), and history fetch (`get_friend_msg_history` /
  `get_group_msg_history`) — everything an agent needs for read → summarize →
  reply, fully mockable.
- **High cohesion, low coupling**: all HTTP lives in
  `cli_anything/qq/utils/qq_backend.py`; the CLI layer never imports requests.
- **Stable JSON contract**: every command emits
  `{"ok", "command", "data", "error"}` with matching success/error ids.

## Known limitations (verified against SnowLuma v2.4.2)

- `set_msg_emoji_like` exists but fails at runtime (retcode=100) — a backend
  limitation, the CLI side is correct and works with NapCat-family backends.
- `translate`, `send_voice`, `send_music`, `get_qq_status`,
  `get_robot_uin_range` are not implemented by the backend; voice can still be
  sent as a `[record]` message segment.
- Calls, Qzone, payments, channels and mini-games are client-only surfaces
  outside any OneBot implementation.

## Compliance

The OneBot bridge attaches to a real QQ client. This is an unofficial protocol;
use may violate QQ's Terms of Service and put the account at risk. This repo
ships a CLI wrapper only — no detection-evasion logic. Operate test accounts at
your own risk.

## License

MIT — see [LICENSE](LICENSE).

# cli-anything-wx

WeChat PC **4.x** harness for AI agents — the WeChat counterpart of
`cli-anything-qq`. Two channels, one stable JSON contract:

| Channel | Mechanism | Needs window? | Commands |
|---|---|---|---|
| **Read** | decrypted local SQLCipher 4 DBs (wechatauto `WeChatDB`) | no | `session list`, `contact search`, `group list/members`, `msg history/new/chats/export`, `db <method>` |
| **Send / act** | UIA + OCR hybrid driving of the logged-in client (wechatauto `WeChat`) | yes (auto-managed) | `send text/file`, `msg recall`, `poke`, `chat open`, `roundtrip` |

See [`skills/SKILL.md`](skills/SKILL.md) — the file agents are meant to read.

## Install

```bash
pip install git+https://github.com/baimocn/cli-anything-extensions.git#subdirectory=wx/agent-harness
# real-machine extras (Windows + WeChat 4.x):
pip install "cli-anything-wx[real]"
```

## Verify (no WeChat needed)

```bash
cd wx/agent-harness && pip install -e . && pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q   # 57 passed, fully mocked
```

## Design notes

- **Library boundary instead of HTTP**: QQ bridges over a pure-HTTP OneBot
  endpoint; WeChat 4.x has no protocol surface, so the control surface is the
  in-process `wechatauto` library. All foreign imports live in
  `cli_anything/wx/utils/wx_backend.py` — the CLI layer never imports
  `wechatauto`, and foreign exceptions are normalized to `RuntimeError`.
- **The topmost rule (verified live)**: an overlapping always-on-top window
  (e.g. an assistant UI) steals every synthetic click the UIA driver makes.
  Every GUI operation therefore runs inside `window_session`, which restores
  the client from the tray, raises it topmost, and reverts afterwards.
- **The filehelper quirk (verified live)**: messages with 文件传输助手 are
  stored under the account's own wxid, not `filehelper`. Display names are
  resolved into ordered candidate keys; `msg history` returns the key that
  held rows.
- **Roundtrip proof**: WeChat has no message-id read-back; `roundtrip` polls
  the decrypted DB for the exact sent content instead.

## Compliance

Unofficial automation of a personal WeChat account. Use may violate WeChat's
Terms of Service and put the account at risk — run test accounts at your own
risk. This package wraps the open-source wechatauto library; it contains no
injection, no hooking, and no detection-evasion logic. The UIA activation
merely enables the standard Windows accessibility tree for the running client.

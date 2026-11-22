# cli-anything-wx — agent-facing skill

Operate **WeChat PC 4.x** entirely from the CLI through two complementary
channels: **decrypted local databases** (reading — deterministic, no UI) and
**UIA driving of the logged-in client** (sending/actions — needs a visible
window). Built on [wechatauto](https://github.com/fanyuantaier/wechatauto-replica)
(wxauto-compatible), the community control surface verified against WeChat
4.1.12+ / 4.2.0.13 / 4.2.0.53.

## Requirements

- Windows 10/11, WeChat PC **4.x** logged in on this machine
- Python 3.10+ (64-bit; 32-bit Python cannot enumerate the 64-bit client)
- Real-machine extras: `pip install "cli-anything-wx[real]"` (wechatauto-replica, comtypes, pywin32)

## Install

```bash
pip install git+https://github.com/baimocn/cli-anything-extensions.git#subdirectory=wx/agent-harness
```

Optional environment overrides (flags `--db-dir` / `--account` win):

```bash
export WX_DB_DIR="D:/wechat-data/xwechat_files"   # default: auto-detect
export WX_ACCOUNT="wxid_xxx_c001"                  # default: auto-pick
export WX_WINDOW_MANAGEMENT=0                      # never touch the window (headless)
```

## Usage

```bash
cli-anything-wx --json status                        # both channels' health
cli-anything-wx --json session list                  # the session panel
cli-anything-wx --json contact search <kw>           # contacts
cli-anything-wx --json group list                    # groups
cli-anything-wx --json group members <group_id>      # group members
cli-anything-wx msg history <who>                    # read chat (human-readable)
cli-anything-wx --json msg history <who> -n 40
cli-anything-wx --json msg new <who> --since <seq>   # incremental read
cli-anything-wx --json msg chats                     # chats holding messages
cli-anything-wx --json send text <who> "hi"          # UIA send
cli-anything-wx --json send file <who> <path>
cli-anything-wx --json msg recall <who>              # recall last sent
cli-anything-wx --json poke <who>
cli-anything-wx --json roundtrip <who> "ping"        # send + DB read-back proof
cli-anything-wx --json db get_sessions --kwargs '{"limit": 5}'
```

`<who>` accepts a display name, remark, wxid, group id, or `filehelper`.

## JSON output contract

Every command emits `{"ok": bool, "command": str, "data": ..., "error": null | {type, message}}`.
Failures set `ok=false` and exit code 1. Stable command ids: `status`,
`session.list`, `contact.search`, `group.list`, `group.members`, `msg.history`,
`msg.new`, `msg.chats`, `msg.export`, `msg.recall`, `send.text`, `send.file`,
`poke`, `chat.open`, `roundtrip`, `db.<method>`, `project.*`, `state.*`.

## Agent workflow: replacing the WeChat GUI

1. **Health check**: `status` — the `db` section must say `decryptable: true`;
   sending additionally needs `window.available: true`.
2. **Discover peers**: `session list` / `contact search` / `group list`.
3. **Read a conversation**: `msg history <who>` — oldest-first lines
   `[MM-DD HH:MM] self|sender: text`, each row carrying `sort_seq` (the poll
   watermark).
4. **Summarize & decide**, then reply via `send text <who> ...`; prove delivery
   with `roundtrip` (send + decrypted-DB read-back) and undo mistakes with
   `msg recall <who>`.
5. **Poll for new activity**: remember the last `sort_seq` per chat and re-run
   `msg new <who> --since <seq>`.

## How the two channels work (agent-relevant internals)

- **Read path**: WeChat 4.x stores messages in local SQLCipher 4 databases
  under `<data dir>/<account-dir>/db_storage/`. wechatauto extracts the keys
  and decrypts them in-process — no UI needed, no injection, works even when
  the client window is hidden.
- **Quirk (verified live)**: traffic with `filehelper` (文件传输助手) is stored
  in the message DB under the **account's own wxid**, not `filehelper`. The
  harness resolves display names into ordered candidate keys and returns the
  key that actually holds rows (`data.key`).
- **Send path**: the client draws its UI with Qt and ships **no UIA tree by
  default**; wechatauto hot-activates a one-byte accessibility gate inside
  the running process (reverts on every client restart — nothing persistent).
  The harness then restores the window from the tray, makes it **topmost**
  (overlapping windows otherwise steal the synthetic clicks — verified live)
  and **borrows the focus for the duration of the operation**, handing both
  back when it finishes. The focus borrow is unavoidable: the Qt accessibility
  tree collapses to two bare panes the moment the window loses focus, so
  there is no background-UIA surface to drive. Reads never touch the window;
  only sends/acts interrupt the desktop for a few seconds. Use `--hide-after`
  (or `WX_HIDE_AFTER_SEND=1`) to also minimize the window back to the tray
  afterwards; set `WX_WINDOW_MANAGEMENT=0` to disable window handling
  entirely (sends will fail whenever something overlaps the window).
- **Roundtrip**: WeChat has no message-id read-back API, so delivery is proven
  by polling the decrypted DB for the exact content within a timeout.

## Agent guidance

- Prefer `--json` for deterministic parsing; `msg history` human output is for
  humans.
- The send path is slow (seconds — UIA + layout calibration); batch reads are
  free. Read via the DB, write via the UIA channel.
- Never paste real wxids into issue reports or shared state files.
- Unofficial automation of a personal account: account risk is the operator's
  own. This repo ships a CLI wrapper only — no detection-evasion logic.

## Known limitations (verified against WeChat 4.2.0.13)

- Sending requires a visible, non-minimized client window on an interactive
  desktop session (services/RDP-disconnected sessions have no UI tree).
- Image/media *download* requires the image AES key (open any image in the
  client once so the client caches it).
- Voice/video calls, Moments *writing*, mini-programs and payments are client
  surfaces the UIA channel does not expose.

## Test workflow

```bash
cd wx/agent-harness && pip install -e . && pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
# 57 tests, fully mocked — no WeChat, no window touches
```

Includes four ablation experiments; see `cli_anything/wx/tests/TEST.md`.
Real-machine E2E (opt-in): `WX_E2E=1 pytest cli_anything/wx/tests/test_full_e2e.py`.

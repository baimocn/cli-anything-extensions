# Test plan — cli-anything-wx

## Scope

`test_core.py` mocks the **foreign surface at the boundary**
(`utils.wx_backend`): `wechatauto` (library) and `win32gui/win32con` (window
layer) are faked; the backend, domain and CLI layers run their real code.
57 tests, no WeChat client, no window touches, no network.

Fake world pieces:

| Fake | Models |
|---|---|
| `FakeDB` | `WeChatDB`: newest-first rows per chat key, contacts/groups/sessions, strictly increasing `sort_seq` |
| `FakeClient` | `WeChat` UIA: **sends only land when the window is topmost** — the live-verified overlap failure mode; filehelper traffic commits under the self wxid |
| `FakeWin32Gui` | window ops recording + topmost gating (`mmui::MainWindow` class) |
| `_SilentClient` | client reports success but never commits — delivery unproven |

## Ablation experiments (each proves one mechanism is necessary)

| # | Ablated mechanism | Expected result | What it proves |
|---|---|---|---|
| 1 | window management (`window_manage=False`) | send raises `foreign window` — clicks "stolen" by the overlap | TOPMOST session is what makes UIA sends land |
| 2 | filehelper→self-wxid key resolution | `read_history("文件传输助手")` finds 0 rows when candidates lack the self wxid | candidate resolution is what makes read history name-agnostic |
| 3 | missing `wechatauto` import | error envelope with the `cli-anything-wx[real]` install hint, exit 1 | the boundary converts library absence into a contract error |
| 4 | backend failure (`RuntimeError` raised by the fake) | `{"ok": false, "command": <matching id>, "error": {type, message}}`, exit 1 | the error contract is an independently verified layer |

## Contract matrix (CLI)

Every `--json` command must emit `{"ok", "command", "data", "error"}` with a
stable command id (`status`, `session.list`, `contact.search`, `group.list`,
`group.members`, `msg.history`, `msg.chats`, `msg.export`, `send.text`,
`roundtrip`, `poke`, `chat.open`, `db.<method>`, `project.*`, `state.*`) and
exit code 1 on failure. Covered one-by-one in `TestCLI` / `TestErrorContract`.

## Path-safety regressions

- `safe_target` rejects absolute paths and `..` segments (traversal segments
  runtime-constructed in tests, never source literals).
- `safe_export_path` rejects `..` but permits resolved absolute destinations
  (exports are explicit agent intent), matching `FakeDB.export_history`'s own
  working-directory whitelist.
- `Session.save_session` keeps the caller's relative project name — storing
  the resolved absolute path would poison the next save (live-caught bug).

## Real-machine E2E (opt-in, gated)

```
WX_E2E=1                  read-only suite: status / session list / history / chats
WX_E2E=1 WX_E2E_SEND=1    + send suite: roundtrip to filehelper (own chat only)
WX_E2E_FORCE=1            turns skips into failures (CI guard)
```

## Run

```bash
cd wx/agent-harness && pip install -e . && pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --basetemp=./.pytest_tmp
# 57 passed
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` avoids third-party plugin breakage
(Python 3.14 + typeguard); `--basetemp` keeps pytest temp dirs off the system
drive. Clean up `./.pytest_tmp` afterwards.

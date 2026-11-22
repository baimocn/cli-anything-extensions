# Test plan — cli-anything-doubao

## Scope

`test_core.py` mocks the CDP boundary (`utils.doubao_backend`) and exercises
the REAL domain + CLI layers. 39 tests, no Doubao app, no network, no
WebSocket. Tests that must exercise real backend logic use function
references captured at import time (`REAL_*`), because the shared fixture
patches the same module attributes.

Fake world: `FakeDoubao` models targets, the sidebar, a **streaming answer**
(fragments arrive per poll, then hold — like the real app), lifecycle
refusals, and a JS-value map for `eval_in_chat`.

## Ablation experiments (each proves one mechanism is necessary)

| # | Ablated mechanism | Expected result | What it proves |
|---|---|---|---|
| 1 | answer-completion detection (`never_stable` stream) | `round_trip_ok: false`, `timeout: true`, partial answer surfaced | the stability poll is what turns streaming text into a verified answer |
| 2 | chat-page target discovery (only a background page exists) | error envelope naming the missing page + seen URLs | target discovery is a verified layer, not an assumption |
| 3 | port-hijack refusal (foreign listener on the debug port) | `launch_app` raises `instead of hijacking` | auto-launch obeys the repo-wide port rule |
| 4 | backend failure → CLI | `{"ok": false, "command": <matching id>, "error": {type, message}}`, exit 1 | the error contract is an independently verified layer |

## Contract matrix (CLI)

Every `--json` command emits `{"ok", "command", "data", "error"}` with a
stable id (`status`, `chat.list`, `chat.new`, `chat.open`, `ask`, `read`,
`history`, `screenshot`, `eval`, `project.*`, `state.*`) and exit code 1 on
failure — covered one-by-one in `TestCLI` / `TestErrorContract`.

## Path-safety regressions

- `safe_target` rejects absolute paths and `..` segments (traversal segments
  runtime-constructed, never source literals).
- `safe_export_path` (screenshot destinations) rejects `..` but permits
  resolved absolute paths.
- `Session.save_session` keeps the caller's relative project name (storing
  the resolved absolute path poisons the next save — live-caught in wx, fixed
  here from the start).
- `_locked_save_json` re-asserts the cwd boundary before `open()` ( Mimosa
  advisory hardening, same as qq/wx).

## Real-machine E2E (opt-in, gated)

```
DOUBAO_E2E=1                    read-only: status / chat list / screenshot
DOUBAO_E2E=1 DOUBAO_E2E_ASK=1   + ask roundtrip (prompt goes to the AI chat)
DOUBAO_E2E_FORCE=1              turns skips into failures
```

## Run

```bash
cd doubao/agent-harness && pip install -e . && pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --basetemp=./.pytest_tmp
# 39 passed
```

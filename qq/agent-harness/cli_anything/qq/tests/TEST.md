# TEST plan — cli-anything-qq

## Test inventory

- `test_core.py` — unit + ablation tests, fully mocked OneBot endpoint. No real
  QQ account required. This is the verification baseline for the CI pipeline.
- `test_full_e2e.py` — optional real-backend E2E; skipped unless a NapCat
  endpoint is reachable and `QQ_E2E=1`.

## Core mechanism under test

The central mechanism is a **pure-HTTP closed loop**:

```
status  -> get_login_info
send    -> send_private_msg / send_group_msg   -> message_id
msg get -> get_msg(message_id)                  -> message content
roundtrip = send + get_msg (two actions, no daemon, no WebSocket)
```

The loop is only useful if it works against *any* endpoint that speaks OneBot
11. `test_core.py` proves this with a stateful in-memory fake (`FakeOneBot`)
that stores messages by id — exactly what a real NapCat backend does.

## Ablation experiments (the load-bearing verification)

| Test | What is removed / broken | Expected result | Proves |
|---|---|---|---|
| `test_ablation_no_token_fails` | access token | `RuntimeError` (retcode != 0) | token is necessary |
| `test_ablation_wrong_endpoint_fails` | endpoint URL | `ConnectionError` | endpoint is necessary |
| `test_ablation_bad_retcode_is_error` | response retcode | CLI JSON `ok=False`, exit 1 | error layer is separate and testable |
| `test_ablation_malformed_body_is_error` | response envelope | `RuntimeError` | envelope parsing is necessary |

Positive control: `TestClosedLoop` runs all three commands plus the full
round-trip and asserts the exact OneBot action sequence
`["send_private_msg", "get_msg"]`. If a future refactor silently adds a daemon
or WebSocket dependency, these tests will still pass (they only exercise
HTTP) — which is the intended guarantee.

## Other coverage

- JSON contract: every command emits `{"ok", "command", "data", "error"}`;
  success and error paths share the same stable `command` id.
- Local state: `Session` snapshot/undo/redo, cross-thread locked saves (4
  threads) never corrupt the project file.
- Security: `project.safe_target` rejects `../` traversal and absolute paths;
  `project_info` masks the access token.

## Running

```bash
cd qq/agent-harness
pip install -e .
pytest cli_anything/qq/tests/test_core.py -v      # deterministic, no network
pytest cli_anything/qq/tests/test_full_e2e.py -v  # optional, real NapCat
```

Expected baseline: `test_core.py` all green on any machine; `test_full_e2e.py`
skipped unless `QQ_E2E=1` with a live endpoint.

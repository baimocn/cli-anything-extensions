# cli-anything-doubao

Doubao desktop (豆包) harness for AI agents — the third app in this repo, next
to `qq` (OneBot HTTP) and `wx` (decrypted DB + UIA). Control surface: the
**Chrome DevTools Protocol** of the app's own Chromium 147 shell. Everything
runs as in-page CDP events, so — like QQ — the whole harness is **fully
background**: typing, clicking, reading and screenshots never touch the OS
window or its focus.

See [`skills/SKILL.md`](skills/SKILL.md) — the file agents are meant to read.

## Install

```bash
pip install git+https://github.com/baimocn/cli-anything-extensions.git#subdirectory=doubao/agent-harness
```

## Verify (no Doubao needed)

```bash
cd doubao/agent-harness && pip install -e . && pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q   # 39 passed, fully mocked CDP
```

## Design notes

- **Why CDP**: the Doubao Windows app is a Chromium shell (chat UI at
  `doubao://doubao-chat/chat`). One launch flag (`--remote-debugging-port`)
  exposes a first-class automation protocol — no injection, no UIA fragility,
  no OCR. The harness only suppresses the WebSocket `Origin` header, which
  new Chromium builds reject (verified live).
- **Completion detection**: Doubao streams answers into the DOM; `ask` waits
  for the `receive_message` count to grow past the pre-send baseline and the
  answer length to hold stable for two consecutive polls. That is the
  Doubao analog of the QQ roundtrip proof (WeChat needs the same trick via
  its decrypted DB, Doubao gets it from the live DOM).
- **SSRF-hardened by construction**: the CDP endpoint is reached through
  stdlib `http.client` with the host as a structured literal and the port
  range-checked per call — there is no URL string to mis-aim.
- **Port safety**: auto-launch probes the port first and refuses to hijack a
  foreign listener (the repo-wide port rule, encoded in the backend).

## Compliance

Drives your own logged-in Doubao account over its own debug interface.
Prompts go to the AI chat only — no messages to third parties. Keep real
account names out of shared files.

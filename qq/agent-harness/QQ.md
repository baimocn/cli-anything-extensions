# QQ harness — methodology and protocol notes

## Why a harness at all

CLI-Anything turns software designed for humans into an Agent-usable CLI by
finding its programmable, headless control surface and wrapping it. QQ has no
official CLI and no official personal-account API, so the control surface
chosen here is the **OneBot 11 HTTP action API** exposed locally by NapCat.

## Control surface

OneBot 11 (HTTP post mode):

```
POST {base_url}/
Content-Type: application/json
Authorization: Bearer <token>

{"action": "get_login_info", "params": {}, "echo": "<uuid>"}

-> {"status": "ok", "retcode": 0, "data": {...}, "echo": "<uuid>"}
```

NapCat is deployed by the user locally. This harness never bundles, starts,
or configures NapCat — it only speaks to an already-running endpoint.

## The minimal closed loop (v1)

OneBot's "read messages" is naturally an event stream (WebSocket), which
would require a resident daemon and a local message store — deliberately
excluded from v1 by the principle of least machinery. Instead the closed loop
is pure HTTP:

1. `status`      -> `get_login_info`            (connectivity + identity)
2. `send`        -> `send_private_msg` / `send_group_msg`  -> `message_id`
3. `msg get`     -> `get_msg(message_id)`        (read back)
4. `roundtrip`   = step 2 + step 3 in one command

Because the read-back is keyed by the `message_id` returned from send, the
loop is self-verifying: a successful `roundtrip` proves both actions worked.
It is also completely mockable — `test_core.py` runs the entire mechanism
against an in-memory fake endpoint with no real QQ account.

## Architecture (high cohesion, low coupling)

```
qq_cli.py            Click commands, JSON envelope, REPL, error handling
   |
   +-- core/qq.py    domain wrappers; assembles action params; send_then_read
   |       |
   |       +-- utils/qq_backend.py   ONLY module that does HTTP
   |
   +-- core/project.py / session.py  local state, locking, history
```

- `qq_backend.py` owns all HTTP and the OneBot envelope; nothing else imports
  `requests`. Config is injected via `BackendConfig`, never module-global.
- `core/qq.py` never touches HTTP; it only assembles params and shapes results.
- `core/project.py` and `core/session.py` are the same locked-state pattern
  used by every harness in this repository (POSIX `flock` / Windows `msvcrt`).

## Verification (ablation-driven)

See `cli_anything/qq/tests/TEST.md`. The four ablations prove that the token,
the endpoint URL, a zero retcode, and a well-formed envelope are each
necessary: removing any one produces the predicted failure.

## Out of scope for v1 (second phase)

- WebSocket event subscription and local message persistence
- Friend/group/contacts management
- Image, voice, and file messages (CQ segments are passed through as-is)
- Multi-account profiles beyond a single configured endpoint

## Compliance

NapCat/OneBot personal-account access is not an official QQ API. Use may
violate QQ terms and risk the account. No evasion logic is included; risk is
the operator's own.

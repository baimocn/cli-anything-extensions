"""Doubao desktop control surface — Chrome DevTools Protocol boundary.

The Doubao Windows app is a Chromium 147 shell whose chat UI lives at
``doubao://doubao-chat/chat``. Launched with ``--remote-debugging-port`` it
exposes the standard CDP, which is the harness's only control surface:

    GET  http://127.0.0.1:{port}/json/list   -> page targets
    WS   /devtools/page/{id}                 -> Runtime.evaluate / Input.* /
                                                Page.captureScreenshot

Everything (typing, clicking, reading the conversation, screenshots) runs as
in-page events — the OS window never needs focus, so the whole harness is
fully background, exactly like the QQ OneBot channel.

Design notes (first principles):
    * One module owns ALL CDP contact; the CLI layer stays protocol-blind and
      foreign exceptions are normalized to RuntimeError.
    * ``websocket-client`` is imported lazily with an actionable error; tests
      mock this module and never need it.
    * HTTP goes through stdlib ``http.client`` with the host as a structured
      literal parameter (never a URL string), so every request lands on the
      local debug port by construction — nothing to mis-aim (SSRF-safe).
    * New Chromium builds reject WebSocket upgrades that carry an ``Origin``
      header — this backend suppresses it (verified live), so no extra flags
      beyond ``--remote-debugging-port`` are needed.

Environment variables (used only by :func:`resolve_config`):
    DOUBAO_CDP_PORT   Debug port (default 9333; probed for availability)
    DOUBAO_APP_PATH   Doubao.exe path for auto-launch (default: launcher next
                      to common install roots, e.g. D:/豆包/Doubao/Doubao.exe)
    DOUBAO_CDP_TIMEOUT  Per-command timeout in seconds (default 25)
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

DEFAULT_PORT = 9333
DEFAULT_TIMEOUT = 25
CHAT_URL_MARKER = "doubao-chat/chat"
_LAUNCH_WAIT_SECONDS = 20

# The endpoint is always the loopback debug port of the local app. The host
# is a structured literal parameter and the port is range-checked before use,
# so every request lands on the local machine by construction.
_LOCAL_HOST = "127.0.0.1"

# DOM contract (verified live on Doubao 2.37.2 / Chromium 147)
EDITOR_SEL = '.tiptap.ProseMirror'
SEND_BTN_SEL = '[data-testid="chat_input_send_button"]'
CONV_ITEM_SEL = '[data-testid="conversation-list-v2-item"]'
NEW_CHAT_SEL = '[data-testid="create_conversation_button"]'
SEND_MSG_SEL = '[data-testid="send_message"]'
RECV_MSG_SEL = '[data-testid="receive_message"]'
MSG_TEXT_SEL = '[data-testid="message_text_content"]'


@dataclass
class BackendConfig:
    """Connection settings for the CDP endpoint."""

    port: int = DEFAULT_PORT
    app_path: str | None = None
    timeout: int = DEFAULT_TIMEOUT


def _validate_port(port: Any) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid CDP port: {port!r}") from exc
    if not 1 <= value <= 65535:
        raise ValueError(f"CDP port out of range: {value}")
    return value


def resolve_config(
    port: int | None = None,
    app_path: str | None = None,
    timeout: int | None = None,
) -> BackendConfig:
    """Resolve backend config from explicit flags, then env, then defaults."""
    try:
        port_val = _validate_port(port or os.environ.get("DOUBAO_CDP_PORT")
                                  or DEFAULT_PORT)
    except ValueError:
        port_val = DEFAULT_PORT
    raw_timeout = timeout or os.environ.get("DOUBAO_CDP_TIMEOUT") or DEFAULT_TIMEOUT
    try:
        timeout_val = int(raw_timeout)
    except (TypeError, ValueError):
        timeout_val = DEFAULT_TIMEOUT
    return BackendConfig(
        port=port_val,
        app_path=(app_path or os.environ.get("DOUBAO_APP_PATH") or None),
        timeout=timeout_val if timeout_val > 0 else DEFAULT_TIMEOUT,
    )


def _guard(fn):
    """Normalize foreign exceptions into RuntimeError for the CLI layer."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (RuntimeError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError(f"doubao backend failure: {type(exc).__name__}: {exc}") from exc

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _import_websocket():
    try:
        from websocket import create_connection  # noqa: PLC0415 — lazy boundary import
    except ImportError as exc:
        raise RuntimeError(
            "websocket-client is not installed: pip install 'cli-anything-doubao'"
        ) from exc
    return create_connection


def _http_get(path: str, port: int, timeout: int) -> tuple[int, bytes]:
    """GET a JSON path from the local CDP endpoint.

    Host is the structured literal ``_LOCAL_HOST`` and the port is validated
    per call — the connection target is fixed by construction.
    """
    conn = http.client.HTTPConnection(_LOCAL_HOST, _validate_port(port),
                                      timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((_LOCAL_HOST, _validate_port(port))) == 0


# ── App lifecycle ───────────────────────────────────────────────────────

def _candidate_app_paths() -> list[str]:
    env = os.environ.get("DOUBAO_APP_PATH")
    if env:
        return [env]
    home_drive = os.path.expandvars("%SystemDrive%") or "C:"
    return [
        "D:/豆包/Doubao/Doubao.exe",
        "D:/Doubao/Doubao/Doubao.exe",
        home_drive + "/豆包/Doubao/Doubao.exe",
        home_drive + "/Doubao/Doubao.exe",
    ]


@_guard
def cdp_alive(config: BackendConfig | None = None) -> bool:
    cfg = config or resolve_config()
    try:
        status, _body = _http_get("/json/version", cfg.port, 3)
        return status == 200
    except OSError:
        return False


@_guard
def launch_app(config: BackendConfig | None = None) -> dict[str, Any]:
    """Start Doubao with the debug port, but only if both are safe.

    The port is probed first — if anything else is already listening on it,
    this refuses to launch (never hijack a foreign listener) and asks for a
    different ``DOUBAO_CDP_PORT``.
    """
    cfg = config or resolve_config()
    if cdp_alive(cfg):
        return {"launched": False, "reason": "already running"}
    if _port_in_use(cfg.port):
        raise RuntimeError(
            f"port {cfg.port} is already used by another program; "
            "set DOUBAO_CDP_PORT to a free port instead of hijacking it"
        )
    app_path = cfg.app_path
    if not app_path:
        for candidate in _candidate_app_paths():
            if os.path.isfile(candidate):
                app_path = candidate
                break
    if not app_path or not os.path.isfile(app_path):
        raise RuntimeError(
            "Doubao.exe not found; start Doubao manually with "
            "--remote-debugging-port=9333 or set DOUBAO_APP_PATH"
        )
    subprocess.Popen(
        [app_path, "--remote-debugging-port=" + str(_validate_port(cfg.port))],
        cwd=os.path.dirname(app_path),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    deadline = time.monotonic() + _LAUNCH_WAIT_SECONDS
    while time.monotonic() < deadline:
        if cdp_alive(cfg):
            return {"launched": True, "app_path": app_path, "port": cfg.port}
        time.sleep(0.8)
    raise RuntimeError(f"Doubao started but CDP did not come up on port {cfg.port}")


@_guard
def ensure_app(config: BackendConfig | None = None) -> dict[str, Any]:
    """Probe; auto-launch when the CDP endpoint is not reachable."""
    cfg = config or resolve_config()
    if cdp_alive(cfg):
        return {"launched": False, "reason": "already running"}
    return launch_app(cfg)


# ── Targets ─────────────────────────────────────────────────────────────

@_guard
def list_pages(config: BackendConfig | None = None) -> list[dict[str, Any]]:
    cfg = config or resolve_config()
    status, body = _http_get("/json/list", cfg.port, cfg.timeout)
    if status != 200:
        raise RuntimeError(f"CDP /json/list returned HTTP {status}")
    return [dict(t) for t in json.loads(body) if isinstance(t, dict)]


@_guard
def find_chat_page(config: BackendConfig | None = None) -> dict[str, Any]:
    """Return the chat page target, launching the app if needed."""
    cfg = config or resolve_config()
    for target in list_pages(cfg):
        if target.get("type") == "page" and CHAT_URL_MARKER in (target.get("url") or ""):
            return target
    raise RuntimeError(
        "Doubao chat page not found. Is the app running and logged in? "
        "Pages seen: " + ", ".join(sorted({t.get("url", "?") for t in list_pages(cfg)}))
    )


# ── CDP session ─────────────────────────────────────────────────────────

_WS_CACHE: dict[str, Any] = {}


def reset_backend_cache() -> None:
    """Drop cached WebSocket sessions (used by tests)."""
    for ws in _WS_CACHE.values():
        try:
            ws.close()
        except Exception:
            pass
    _WS_CACHE.clear()


def _session(target_id: str, config: BackendConfig | None = None):
    cfg = config or resolve_config()
    cached = _WS_CACHE.get(target_id)
    if cached is not None:
        try:
            cached.ping()
            return cached
        except Exception:
            try:
                cached.close()
            except Exception:
                pass
            _WS_CACHE.pop(target_id, None)
    create_connection = _import_websocket()
    ws = create_connection(
        "ws://" + _LOCAL_HOST + ":" + str(_validate_port(cfg.port))
        + "/devtools/page/" + quote(target_id),
        timeout=cfg.timeout,
        suppress_origin=True,
    )
    _WS_CACHE[target_id] = ws
    return ws


class _Cmd:
    """Minimal request/response multiplexer over one CDP WebSocket."""

    def __init__(self, ws):
        self._ws = ws
        self._id = 0

    def call(self, method: str, params: dict[str, Any] | None = None,
             timeout: int | None = None) -> dict[str, Any]:
        self._id += 1
        msg = json.dumps({"id": self._id, "method": method,
                          "params": params or {}})
        self._ws.send(msg)
        deadline = time.monotonic() + (timeout or 25)
        while True:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            self._ws.settimeout(remaining / 1000)
            resp = json.loads(self._ws.recv())
            if resp.get("id") == self._id:
                if "error" in resp:
                    raise RuntimeError(
                        f"CDP {method} error: {resp['error'].get('message')}"
                    )
                return resp.get("result") or {}


def _eval(cmd: _Cmd, expression: str, await_promise: bool = True) -> Any:
    result = cmd.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True,
         "awaitPromise": await_promise},
    )
    inner = result.get("result") or {}
    if inner.get("subtype") == "error":
        raise RuntimeError(f"page JS error: {str(inner.get('description'))[:300]}")
    if "value" in inner:
        return inner["value"]
    return None


# ── Thin page operations (the only functions the domain layer calls) ────

@_guard
def eval_in_chat(expression: str, config: BackendConfig | None = None) -> Any:
    """Evaluate JS in the chat page and return the JSON value."""
    cfg = config or resolve_config()
    target = find_chat_page(cfg)
    ws = _session(target["id"], cfg)
    return _eval(_Cmd(ws), expression)


@_guard
def screenshot(config: BackendConfig | None = None) -> bytes:
    """Capture the chat page as PNG (fully background)."""
    cfg = config or resolve_config()
    target = find_chat_page(cfg)
    ws = _session(target["id"], cfg)
    result = _Cmd(ws).call("Page.captureScreenshot", {"format": "png"})
    import base64  # noqa: PLC0415

    return base64.b64decode(result["data"])


# ── DOM contract helpers (JS snippets the domain layer composes) ───────

_JS_CONVERSATIONS = f"""
(() => {{
  const items = [...document.querySelectorAll('{CONV_ITEM_SEL}')]
  // the canonical title lives in span.whitespace-nowrap; the item also holds
  // measure/sr copies that repeat the text 2-3x — prefer the clean node
  return items.map((el, idx) => {{
    const node = el.querySelector('span.whitespace-nowrap') || el
    return {{ index: idx, title: node.textContent.trim().slice(0, 80) }}
  }}).filter(c => c.title)
}})()
"""

_JS_NEW_CHAT = f"""
(() => {{
  const b = document.querySelector('{NEW_CHAT_SEL}')
  if (b) {{ b.click(); return true }}
  return false
}})()
"""

_JS_OPEN_CONVERSATION = """
(index) => {
  const items = [...document.querySelectorAll('%s')]
  const el = items[index]
  if (!el) return false
  const target = el.querySelector('a[href]') || el
  target.click()
  return true
}
""" % CONV_ITEM_SEL

_JS_EDITOR_READY = f"""
(() => {{
  const ed = document.querySelector('{EDITOR_SEL}')
  return !!ed
}})()
"""

_JS_SEND = f"""
(() => {{
  const ed = document.querySelector('{EDITOR_SEL}')
  if (!ed) return {{ok: false, reason: 'editor not found'}}
  ed.focus()
  const btn = document.querySelector('{SEND_BTN_SEL}')
  if (!btn || btn.disabled || btn.getAttribute('aria-disabled') === 'true') {{
    return {{ok: false, reason: 'send button missing or disabled'}}
  }}
  btn.click()
  return {{ok: true}}
}})()
"""

_JS_MESSAGES = f"""
(() => {{
  const textOf = (root) => {{
    if (!root) return ''
    const inner = root.querySelector('{MSG_TEXT_SEL}')
    return (inner || root).innerText.trim()
  }}
  const sends = [...document.querySelectorAll('{SEND_MSG_SEL}')]
  const recvs = [...document.querySelectorAll('{RECV_MSG_SEL}')]
  return {{
    send_count: sends.length,
    recv_count: recvs.length,
    last_recv: textOf(recvs[recvs.length - 1]),
    last_recv_len: textOf(recvs[recvs.length - 1]).length,
    last_send: textOf(sends[sends.length - 1]),
  }}
}})()
"""

_JS_INPUT_STATE = f"""
(() => {{
  const ed = document.querySelector('{EDITOR_SEL}')
  const btn = document.querySelector('{SEND_BTN_SEL}')
  return {{
    editor_ready: !!ed,
    editor_text: ed ? ed.innerText.trim() : null,
    has_send: !!btn,
    send_disabled: btn ? (btn.disabled || btn.getAttribute('aria-disabled') === 'true') : null,
  }}
}})()
"""


@_guard
def get_conversations(config: BackendConfig | None = None) -> list[dict[str, Any]]:
    return eval_in_chat(_JS_CONVERSATIONS, config) or []


@_guard
def new_chat(config: BackendConfig | None = None) -> bool:
    return bool(eval_in_chat(_JS_NEW_CHAT, config))


@_guard
def open_conversation(index: int, config: BackendConfig | None = None) -> bool:
    """Open the conversation at the sidebar index (DOM click, background)."""
    cfg = config or resolve_config()
    target = find_chat_page(cfg)
    ws = _session(target["id"], cfg)
    cmd = _Cmd(ws)
    result = cmd.call(
        "Runtime.evaluate",
        {"expression": _JS_OPEN_CONVERSATION, "returnByValue": True,
         "awaitPromise": True, "userGesture": True,
         "arguments": [{"value": int(index)}]},
    )
    return bool((result.get("result") or {}).get("value"))


@_guard
def get_messages(config: BackendConfig | None = None) -> dict[str, Any]:
    return eval_in_chat(_JS_MESSAGES, config) or {}


@_guard
def get_input_state(config: BackendConfig | None = None) -> dict[str, Any]:
    return eval_in_chat(_JS_INPUT_STATE, config) or {}


@_guard
def editor_ready(config: BackendConfig | None = None) -> bool:
    return bool(eval_in_chat(_JS_EDITOR_READY, config))


@_guard
def insert_text(text: str, config: BackendConfig | None = None) -> None:
    """Type into the editor with CDP Input events (page-level, no OS focus)."""
    cfg = config or resolve_config()
    target = find_chat_page(cfg)
    ws = _session(target["id"], cfg)
    cmd = _Cmd(ws)
    focus_expr = "document.querySelector('" + EDITOR_SEL + "').focus()"
    cmd.call("Runtime.evaluate", {"expression": focus_expr})
    cmd.call("Input.insertText", {"text": text})


@_guard
def click_send(config: BackendConfig | None = None) -> dict[str, Any]:
    return eval_in_chat(_JS_SEND, config) or {}

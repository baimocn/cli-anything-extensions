"""Domain layer for the Doubao harness.

Thin orchestration on top of ``utils.doubao_backend``: it composes the DOM
contract selectors into page JS, polls for response completion, and renders
conversation history — never importing CDP/websocket machinery itself.

The central mechanism is :func:`ask_and_wait`: type via ``Input.insertText``
(page-level events, no OS focus), click the send button, then poll the chat
DOM until the assistant's answer stops growing — the Doubao analog of the QQ
harness's send → ``get_msg`` read-back roundtrip.
"""

from __future__ import annotations

import time
from typing import Any

from cli_anything.doubao.core import project as project_mod
from cli_anything.doubao.utils import doubao_backend as backend

_POLL_INTERVAL = 1.2
_STABLE_ROUNDS = 2  # answer length must hold this many polls to count as done


def status(config: backend.BackendConfig | None = None,
           launch: bool = False) -> dict[str, Any]:
    """Health of the control surface: CDP endpoint, chat page, editor state.

    Each section degrades independently; failures are collected in ``errors``
    so ``status`` always answers.
    """
    errors: list[str] = []
    launched: dict[str, Any] | None = None
    if launch:
        try:
            launched = backend.ensure_app(config)
        except Exception as exc:
            errors.append(f"launch: {exc}")
    try:
        alive = backend.cdp_alive(config)
    except Exception as exc:
        alive = False
        errors.append(f"cdp probe: {exc}")
    page: dict[str, Any] = {}
    if alive:
        try:
            target = backend.find_chat_page(config)
            page = {"found": True, "id": target.get("id"), "url": target.get("url")}
        except Exception as exc:
            page = {"found": False}
            errors.append(f"page: {exc}")
    input_state: dict[str, Any] = {}
    if page.get("found"):
        try:
            input_state = backend.get_input_state(config)
        except Exception as exc:
            errors.append(f"input: {exc}")
    return {
        "cdp_alive": alive,
        "port": (config or backend.resolve_config()).port,
        "launched": launched,
        "page": page,
        "input": input_state,
        "errors": errors,
    }


def _dedupe_title(title: str) -> str:
    """Collapse DOM measure/sr-only copies ("标题标题标题" -> "标题").

    The sidebar renders the title into several stacked copies for ellipsis
    measurement; textContent therefore repeats it. Detect the exact
    repetition and keep the unit.
    """
    if not title:
        return title
    for unit in range(1, len(title) // 2 + 1):
        if len(title) % unit == 0 and title[:unit] * (len(title) // unit) == title:
            return title[:unit]
    return title


def list_conversations(config: backend.BackendConfig | None = None) -> list[dict[str, Any]]:
    """Sidebar conversation entries (index, title) — the chat history panel."""
    conversations = backend.get_conversations(config)
    for conv in conversations:
        conv["title"] = _dedupe_title(str(conv.get("title") or ""))
    return conversations


def new_chat(config: backend.BackendConfig | None = None) -> dict[str, Any]:
    """Start a fresh conversation (the sidebar's new-chat button)."""
    return {"clicked": backend.new_chat(config)}


def open_conversation(index: int, config: backend.BackendConfig | None = None) -> dict[str, Any]:
    return {"opened": backend.open_conversation(index, config), "index": int(index)}


def read_answer(config: backend.BackendConfig | None = None) -> dict[str, Any]:
    """The latest assistant answer in the currently open conversation."""
    data = backend.get_messages(config)
    return {
        "send_count": data.get("send_count"),
        "recv_count": data.get("recv_count"),
        "last_user_message": data.get("last_send"),
        "last_answer": data.get("last_recv"),
    }


def read_conversation(limit: int = 40,
                      config: backend.BackendConfig | None = None) -> dict[str, Any]:
    """The open conversation as an ordered, agent-readable transcript.

    One JS pass walks the DOM in order and tags each message as ``user`` or
    ``assistant`` — oldest-first, each entry trimmed to a readable size.
    """
    js = (
        "(() => {"
        f"  const sel = '{backend.SEND_MSG_SEL}, {backend.RECV_MSG_SEL}';"
        f"  const textSel = '{backend.MSG_TEXT_SEL}';"
        "  const nodes = [...document.querySelectorAll(sel)];"
        "  const textOf = (r) => { const i = r.querySelector(textSel);"
        "    return (i || r).innerText.trim(); };"
        "  return nodes.map((n) => ({"
        "    role: n.getAttribute('data-testid') === 'send_message' ? 'user' : 'assistant',"
        "    text: textOf(n).slice(0, 4000),"
        "  }));"
        "})()"
    )
    messages = backend.eval_in_chat(js, config) or []
    if limit and len(messages) > limit:
        messages = messages[-limit:]
    return {"count": len(messages), "messages": messages}


def send_prompt(text: str, config: backend.BackendConfig | None = None) -> dict[str, Any]:
    """Compose ``text`` into the editor and click send (background-safe)."""
    backend.insert_text(text, config)
    time.sleep(0.4)
    result = backend.click_send(config)
    if not result.get("ok"):
        raise RuntimeError(f"send failed: {result.get('reason')}")
    return {"sent": text, "send_result": result}


def ask_and_wait(
    text: str,
    timeout: float = 90.0,
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """The full closed loop: send a prompt, wait for the answer to finish.

    Completion is detected from the DOM: the ``receive_message`` count must
    grow past the pre-send baseline and the answer's length must hold stable
    for :data:`_STABLE_ROUNDS` consecutive polls (streaming text grows; a
    finished answer does not). Raises on timeout with the partial answer
    attached so the caller can still see what arrived.
    """
    baseline = backend.get_messages(config)
    baseline_recv = int(baseline.get("recv_count") or 0)
    send_prompt(text, config)
    deadline = time.monotonic() + timeout
    stable = 0
    last_len = -1
    last_text = ""
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL)
        data = backend.get_messages(config)
        recv = int(data.get("recv_count") or 0)
        last_text = str(data.get("last_recv") or "")
        if recv <= baseline_recv:
            stable = 0
            continue
        if len(last_text) == last_len and last_len > 0:
            stable += 1
            if stable >= _STABLE_ROUNDS:
                return {
                    "question": text,
                    "answer": last_text,
                    "recv_count": recv,
                    "round_trip_ok": True,
                    "elapsed_s": round(timeout - (deadline - time.monotonic()), 1),
                }
        else:
            stable = 0
        last_len = len(last_text)
    return {
        "question": text,
        "answer": last_text,
        "recv_count": int(backend.get_messages(config).get("recv_count") or 0),
        "round_trip_ok": False,
        "timeout": True,
    }


def screenshot(out_path: str, config: backend.BackendConfig | None = None) -> dict[str, Any]:
    """Capture the chat page to ``out_path`` (validated export path)."""
    target = project_mod.safe_export_path(out_path)
    data = backend.screenshot(config)
    target.write_bytes(data)
    return {"path": str(target), "bytes": len(data)}


def raw_eval(expression: str, config: backend.BackendConfig | None = None) -> Any:
    """Read-only-ish passthrough for one-off page JS (escape hatch).

    Mirrors the QQ harness's ``call`` command: occasionally an agent needs a
    DOM probe the wrappers don't cover. Compose carefully — this executes in
    the chat page with full DOM access.
    """
    return backend.eval_in_chat(expression, config)

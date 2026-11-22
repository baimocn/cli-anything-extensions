"""OneBot 11 HTTP action client for QQ (via NapCat / Lagrange).

This is the single module that owns ALL HTTP communication with the OneBot
implementation. It speaks the OneBot 11 *action* API over HTTP POST:

    POST {base_url}/   {"action": ..., "params": ..., "echo": ...}
    -> {"status": "ok", "retcode": 0, "data": ..., "echo": ...}

Design notes (first principles):
    * No daemon, no WebSocket. The whole round-trip is pure HTTP, so the
      harness stays stateless and trivially mockable in tests.
    * OneBot is the only mechanism here. The CLI layer never touches HTTP;
      it only calls the thin wrappers at the bottom of this file.
    * Configuration is injectable via ``BackendConfig``. The CLI resolves it
      from flags/env, tests pass a mock endpoint; no module-level global state.

Environment variables (used only by :func:`resolve_config`):
    QQ_NAPCAT_URL    OneBot HTTP endpoint (default http://127.0.0.1:3000)
    QQ_NAPCAT_TOKEN  Optional access token, sent as ``Authorization: Bearer``
    QQ_NAPCAT_TIMEOUT Request timeout in seconds (default 30)
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_TIMEOUT = 30


def _env_timeout() -> int:
    raw = os.environ.get("QQ_NAPCAT_TIMEOUT", str(DEFAULT_TIMEOUT))
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


@dataclass
class BackendConfig:
    """Connection settings for the OneBot HTTP endpoint."""

    base_url: str = DEFAULT_BASE_URL
    access_token: str | None = None
    timeout: int = DEFAULT_TIMEOUT


def resolve_config(
    base_url: str | None = None,
    access_token: str | None = None,
    timeout: int | None = None,
) -> BackendConfig:
    """Resolve backend config from explicit flags, then env, then defaults.

    ``None`` means "not supplied" (fall through to env). An explicit empty
    string is also treated as not configured, which keeps the CLI flags
    simple (``--url ""`` -> default endpoint).
    """
    url = (base_url or os.environ.get("QQ_NAPCAT_URL") or DEFAULT_BASE_URL).rstrip("/")
    token = access_token or os.environ.get("QQ_NAPCAT_TOKEN") or None
    return BackendConfig(
        base_url=url,
        access_token=token,
        timeout=timeout if timeout and timeout > 0 else _env_timeout(),
    )


def _error_message(body: dict[str, Any]) -> str:
    """Extract a human-readable error from a non-zero OneBot response."""
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("message", "wording", "error", "msg"):
            if data.get(key):
                return str(data[key])
    if isinstance(data, str) and data:
        return data
    return (
        f"OneBot action failed (retcode={body.get('retcode')}, "
        f"status={body.get('status')})"
    )


def api_request(
    action: str,
    params: dict[str, Any] | None = None,
    config: BackendConfig | None = None,
) -> dict[str, Any]:
    """Call a OneBot 11 action over HTTP and return its ``data`` payload.

    Raises:
        RuntimeError: on non-zero ``retcode`` or a non-JSON / malformed body.
        requests.exceptions.ConnectionError: when the endpoint is unreachable
            (kept as-is so the CLI layer can give a clear install hint).
        requests.exceptions.HTTPError: on a non-2xx HTTP status.
    """
    cfg = config or resolve_config()
    payload = {
        "action": action,
        "params": params or {},
        "echo": uuid.uuid4().hex,
    }
    headers = {"Content-Type": "application/json"}
    if cfg.access_token:
        headers["Authorization"] = f"Bearer {cfg.access_token}"

    # OneBot 11 HTTP posts to the root path of the configured endpoint.
    resp = requests.post(
        cfg.base_url + "/",
        json=payload,
        headers=headers,
        timeout=cfg.timeout,
    )
    resp.raise_for_status()

    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError(
            f"OneBot returned non-JSON response: {resp.text[:200]!r}"
        ) from exc
    if not isinstance(body, dict):
        raise RuntimeError(
            f"OneBot returned unexpected response type: {type(body).__name__}"
        )

    retcode = body.get("retcode", -1)
    if retcode != 0:
        raise RuntimeError(_error_message(body))

    data = body.get("data")
    return data if isinstance(data, (dict, list)) else {}


# ── Thin action wrappers (the only functions the CLI layer calls) ──────

def get_login_info(config: BackendConfig | None = None) -> dict[str, Any]:
    """Return the logged-in account's info (also serves as a health check)."""
    return api_request("get_login_info", {}, config)


def send_private_msg(
    user_id: int | str,
    message: str | list[dict[str, Any]],
    config: BackendConfig | None = None,
) -> dict[str, Any]:
    """Send a private message; returns ``{"message_id": ...}``."""
    return api_request(
        "send_private_msg",
        {"user_id": int(user_id), "message": message},
        config,
    )


def send_group_msg(
    group_id: int | str,
    message: str | list[dict[str, Any]],
    config: BackendConfig | None = None,
) -> dict[str, Any]:
    """Send a group message; returns ``{"message_id": ...}``."""
    return api_request(
        "send_group_msg",
        {"group_id": int(group_id), "message": message},
        config,
    )


def get_msg(
    message_id: int | str,
    config: BackendConfig | None = None,
) -> dict[str, Any]:
    """Fetch a message by id; used to read back a just-sent message."""
    return api_request("get_msg", {"message_id": int(message_id)}, config)


def get_friend_list(config: BackendConfig | None = None) -> list[dict[str, Any]]:
    """List the account's friends."""
    data = api_request("get_friend_list", {}, config)
    return data.get("items") if isinstance(data, dict) and "items" in data else (data if isinstance(data, list) else [])


def get_group_list(config: BackendConfig | None = None) -> list[dict[str, Any]]:
    """List the account's groups."""
    data = api_request("get_group_list", {}, config)
    return data.get("items") if isinstance(data, dict) and "items" in data else (data if isinstance(data, list) else [])


def get_group_member_list(
    group_id: int | str,
    config: BackendConfig | None = None,
) -> list[dict[str, Any]]:
    """List members of a group."""
    data = api_request("get_group_member_list", {"group_id": int(group_id)}, config)
    if isinstance(data, dict):
        return data.get("members") or data.get("list") or []
    return data if isinstance(data, list) else []


def _msg_history(action: str, peer_key: str, peer_id: int | str,
                 count: int, config: BackendConfig | None) -> list[dict[str, Any]]:
    """Shared fetch for get_friend_msg_history / get_group_msg_history.

    Returns the ``messages`` list (empty when the peer has no history).
    """
    data = api_request(action, {peer_key: int(peer_id), "count": int(count)}, config)
    if isinstance(data, dict):
        return data.get("messages") or []
    return data if isinstance(data, list) else []


def get_friend_msg_history(
    user_id: int | str,
    count: int = 20,
    config: BackendConfig | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent private-message history with a friend."""
    return _msg_history("get_friend_msg_history", "user_id", user_id, count, config)


def get_group_msg_history(
    group_id: int | str,
    count: int = 20,
    config: BackendConfig | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent group-message history for a group."""
    return _msg_history("get_group_msg_history", "group_id", group_id, count, config)

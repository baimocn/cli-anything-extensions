"""Domain layer for the WeChat harness.

Thin orchestration on top of ``utils.wx_backend``: it owns rendering and the
closed loop, and never imports ``wechatauto`` itself. The central mechanism is
:func:`send_and_verify` — drive the real client (UIA), then prove the message
landed by reading it back from the decrypted message database. That is the
WeChat analog of the QQ harness's send → ``get_msg`` read-back roundtrip,
because WeChat 4.x has no message-id read-back API.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from cli_anything.wx.utils import wx_backend as backend

_SELF_MARKERS = ("成功", "success", "ok")


def status(config: backend.BackendConfig | None = None) -> dict[str, Any]:
    """Health report across both channels: decrypted DB + client window.

    Each channel degrades independently; failures are collected in
    ``errors`` instead of aborting, so ``status`` always answers.
    """
    errors: list[str] = []
    db_info: dict[str, Any] = {}
    try:
        info = backend.get_self_info(config)
        db_info["wxid"] = info.get("wxid")
        db_info["nickname"] = info.get("nickname") or info.get("NickName") or None
        db_info["message_chats"] = len(backend.list_message_chats(config))
        db_info["decryptable"] = True
    except Exception as exc:
        db_info["decryptable"] = False
        errors.append(f"db: {exc}")
    try:
        win = backend.window_status(config)
    except Exception as exc:
        win = {"available": False, "reason": str(exc)}
    return {
        "db": db_info,
        "window": win,
        "send_path": "uia" if win.get("available") else "unavailable",
        "read_path": "db-decrypt" if db_info.get("decryptable") else "unavailable",
        "errors": errors,
    }


# ── Sessions / contacts / groups (replace the GUI's left panel) ─────────

def list_sessions(
    limit: int = 100, config: backend.BackendConfig | None = None
) -> list[dict[str, Any]]:
    rows = backend.get_sessions(limit, config)
    return [
        {
            "username": s.get("username"),
            "name": s.get("name") or s.get("nick") or None,
            "unread": s.get("unread"),
            "summary": s.get("summary"),
            "last_time": s.get("last_time"),
        }
        for s in rows
    ]


def search_contacts(
    keyword: str, config: backend.BackendConfig | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "username": c.get("username"),
            "nickname": c.get("nickname") or c.get("NickName"),
            "remark": c.get("remark") or c.get("Remark"),
            "type": c.get("type") or c.get("Type"),
        }
        for c in backend.search_contacts(keyword, config)
    ]


def list_groups(config: backend.BackendConfig | None = None) -> list[dict[str, Any]]:
    return [
        {
            "username": g.get("username") or g.get("chatroom_wxid") or g.get("wxid"),
            "name": g.get("nickname") or g.get("name") or g.get("NickName"),
        }
        for g in backend.get_groups(config)
    ]


def list_group_members(
    group_id: str, config: backend.BackendConfig | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "username": m.get("username") or m.get("wxid"),
            "nickname": m.get("nickname") or m.get("display_name") or m.get("NickName"),
        }
        for m in backend.get_group_members(group_id, config)
    ]


# ── Message reading (replace the GUI's chat panel) ──────────────────────

def _stamp(epoch_seconds: Any) -> str:
    if isinstance(epoch_seconds, (int, float)) and epoch_seconds > 0:
        return _dt.datetime.fromtimestamp(epoch_seconds, _dt.timezone.utc).astimezone().strftime(
            "%m-%d %H:%M"
        )
    return ""


def _compact_row(row: dict[str, Any], self_wxid: str | None) -> dict[str, Any]:
    sender_username = row.get("sender_username")
    is_self = bool(sender_username) and sender_username == self_wxid
    sender = row.get("sender_username") or ("self" if is_self else "?")
    return {
        "time": _stamp(row.get("create_time")),
        "sender": sender,
        "is_self": is_self,
        "type": row.get("type"),
        "sort_seq": row.get("sort_seq"),
        "local_id": row.get("local_id"),
        "shard": row.get("shard"),
        "text": row.get("content"),
    }


def read_history(
    who: str,
    count: int = 20,
    offset: int = 0,
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """Read recent messages with *who*, oldest-first, agent-readable.

    The message DB keys chats by username; display names (and filehelper,
    which 4.x stores under the account's own wxid) are resolved into ordered
    candidates and the first candidate holding rows wins.
    """
    resolution = backend.resolve_chat_key(who, config)
    self_wxid = resolution.get("self_wxid")
    candidates = resolution["candidates"] or [who]
    rows: list[dict[str, Any]] = []
    used_key: str | None = None
    for key in candidates:
        try:
            rows = backend.get_messages(key, limit=count, offset=offset, config=config)
        except RuntimeError:
            continue
        if rows:
            used_key = key
            break
    entries = [_compact_row(r, self_wxid) for r in rows]
    # get_messages returns newest-first (sort_seq DESC); reading is oldest-first.
    entries.reverse()
    return {
        "who": who,
        "key": used_key,
        "resolved_candidates": candidates,
        "count": len(entries),
        "messages": entries,
    }


def read_new(
    who: str, since_seq: int, limit: int = 200,
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """Incremental read: rows with sort_seq strictly greater than since_seq."""
    resolution = backend.resolve_chat_key(who, config)
    self_wxid = resolution.get("self_wxid")
    rows: list[dict[str, Any]] = []
    used_key: str | None = None
    for key in resolution["candidates"] or [who]:
        try:
            rows = backend.get_new_messages(key, since_seq=since_seq, limit=limit, config=config)
        except RuntimeError:
            continue
        if rows:
            used_key = key
            break
    entries = [_compact_row(r, self_wxid) for r in rows]
    entries.reverse()
    return {"who": who, "key": used_key, "since_seq": since_seq,
            "count": len(entries), "messages": entries}


def list_message_chats(config: backend.BackendConfig | None = None) -> list[dict[str, Any]]:
    """Chats that actually hold messages in the local DB (with counts)."""
    return backend.list_message_chats(config)


def export_history(
    out_path: str,
    users: list[str] | None = None,
    limit_per_chat: int | None = None,
    fmt: str = "json",
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """Full-fidelity history export (JSON/SQLite) via the library."""
    return backend.export_history(out_path, users=users, limit_per_chat=limit_per_chat,
                                  fmt=fmt, config=config)


# ── Send path (UIA) + the closed loop ───────────────────────────────────

def send_text(
    who: str | None,
    text: str,
    verify: bool = False,
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """Send a text message through the real client."""
    resp = backend.send_text(text, who, verify=verify, config=config)
    return {"sent": resp, "status": resp.get("status"), "to": who}


def send_file(
    who: str | None,
    file_path: str,
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    resp = backend.send_file(file_path, who, config=config)
    return {"sent": resp, "status": resp.get("status"), "to": who, "file": file_path}


def recall_last(who: str | None, config: backend.BackendConfig | None = None) -> dict[str, Any]:
    resp = backend.recall_last(who, config=config)
    return {"sent": resp, "status": resp.get("status"), "to": who}


def poke(who: str | None, config: backend.BackendConfig | None = None) -> dict[str, Any]:
    resp = backend.poke(who, config=config)
    return {"sent": resp, "status": resp.get("status"), "to": who}


def chat_open(who: str, config: backend.BackendConfig | None = None) -> dict[str, Any]:
    return backend.chat_open(who, config=config)


def ui_messages(
    who: str, limit: int = 10, config: backend.BackendConfig | None = None
) -> dict[str, Any]:
    return backend.ui_messages(who, limit, config=config)


def send_and_verify(
    who: str,
    text: str,
    timeout: float = 12.0,
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """The full closed loop: UIA send, then DB read-back proof.

    Raises when the send itself fails; a ``round_trip_ok: false`` result means
    the client reported success but the message has not been committed to the
    local DB within *timeout* seconds (WAL lag or delivery failure).
    """
    try:
        sent = send_text(who, text, config=config)
    except RuntimeError as exc:
        raise RuntimeError(f"send failed: {exc}") from exc
    client_status = str(sent.get("status") or "")
    if client_status and not any(m in client_status.lower() for m in _SELF_MARKERS):
        raise RuntimeError(f"send failed: {sent['sent'].get('message', client_status)}")
    verified = backend.find_in_db(who, text, timeout=timeout, config=config)
    return {
        "sent": sent,
        "read_back": verified.get("found"),
        "key": verified.get("key"),
        "round_trip_ok": verified.get("found") is not None,
    }


def db_passthrough(method: str, kwargs: dict[str, Any],
                   config: backend.BackendConfig | None = None) -> Any:
    """Read-only WeChatDB passthrough (see backend whitelist)."""
    return backend.db_passthrough(method, kwargs, config)

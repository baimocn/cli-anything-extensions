"""Domain layer for QQ messaging.

Thin wrappers that assemble OneBot action parameters and delegate all HTTP to
``utils.qq_backend``. This module holds no HTTP code and no global state — it
is intentionally the smallest surface that the CLI talks to.

The round-trip helper :func:`send_then_read` is the central mechanism that the
ablation tests validate: send a message, obtain its ``message_id``, then read
it back with ``get_msg``. No daemon and no WebSocket needed.
"""

from __future__ import annotations

import os
from typing import Any

from cli_anything.qq.utils import qq_backend as backend


def normalize_message(message: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Accept a plain string or an OneBot CQ-message segment list unchanged."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return message
    raise ValueError("message must be a string or a list of OneBot message segments")


def login_status(config: backend.BackendConfig | None = None) -> dict[str, Any]:
    """Return identity info; doubles as a connectivity probe."""
    data = backend.get_login_info(config)
    return {
        "user_id": data.get("user_id"),
        "nickname": data.get("nickname"),
        "connected": True,
    }


def send_private(
    user_id: int | str,
    message: str | list[dict[str, Any]],
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    data = backend.send_private_msg(user_id, normalize_message(message), config)
    return {
        "message_id": data.get("message_id"),
        "target": "private",
        "user_id": int(user_id),
    }


def send_group(
    group_id: int | str,
    message: str | list[dict[str, Any]],
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    data = backend.send_group_msg(group_id, normalize_message(message), config)
    return {
        "message_id": data.get("message_id"),
        "target": "group",
        "group_id": int(group_id),
    }


def read_message(
    message_id: int | str,
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """Fetch a message by id and return a compact, stable shape."""
    data = backend.get_msg(message_id, config)
    return {
        "message_id": data.get("message_id"),
        "message_type": data.get("message_type"),
        "user_id": data.get("user_id"),
        "group_id": data.get("group_id"),
        "message": data.get("message"),
        "raw": data,
    }


def send_then_read(
    target: str,
    peer_id: int | str,
    message: str | list[dict[str, Any]],
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """The full closed loop: send a message, then read it back by message_id.

    ``target`` is ``"private"`` or ``"group"``. Raises if either the send or the
    read-back fails, so a successful result is proof that both actions worked.
    """
    if target == "private":
        sent = send_private(peer_id, message, config)
    elif target == "group":
        sent = send_group(peer_id, message, config)
    else:
        raise ValueError("target must be 'private' or 'group'")

    message_id = sent.get("message_id")
    if message_id is None:
        raise RuntimeError("OneBot did not return a message_id; cannot read back")

    read_back = read_message(message_id, config)
    return {
        "sent": sent,
        "read_back": read_back,
        "round_trip_ok": (
            str(read_back.get("message_id")) == str(message_id)
        ),
    }


def raw_action(
    action: str,
    params: dict[str, Any] | None = None,
    config: backend.BackendConfig | None = None,
) -> Any:
    """Passthrough for any OneBot action the backend supports.

    This is the escape hatch that exposes the full action surface (poke,
    ban, emoji-like, file upload, OCR, ...) without wrapping each one.
    """
    return backend.api_request(action, params or {}, config)


# ── v3: high-value actions (revoke, react, poke, group admin, files) ────
# Each helper assembles OneBot params and delegates to the backend. Only the
# frequently-used surface gets a typed wrapper; everything else goes through
# raw_action().

def revoke_message(message_id: int | str, config=None) -> dict[str, Any]:
    """Recall a message (delete_msg)."""
    return backend.api_request("delete_msg", {"message_id": int(message_id)}, config)


def mark_read(message_id: int | str, config=None) -> dict[str, Any]:
    """Mark a message as read (mark_msg_as_read)."""
    return backend.api_request("mark_msg_as_read", {"message_id": int(message_id)}, config)


def emoji_like(message_id: int | str, emoji_id: str = "4",
               remove: bool = False, config=None) -> dict[str, Any]:
    """React to a message with a QQ face; emoji_id 4 is the thumbs-up."""
    return backend.api_request(
        "set_msg_emoji_like",
        {"message_id": int(message_id), "emoji_id": str(emoji_id), "set": not remove},
        config,
    )


def ocr_image(image: str, config=None) -> dict[str, Any]:
    """Run OCR over an image URL or file path."""
    return backend.api_request("ocr_image", {"image": image}, config)


def poke(target: str, group_id: int | str | None, user_id: int | str,
         config=None) -> dict[str, Any]:
    """Poke a user; target 'private' or 'group' (group pokes need both ids)."""
    if target == "group":
        if group_id is None:
            raise ValueError("group poke requires --group")
        return backend.api_request(
            "group_poke", {"group_id": int(group_id), "user_id": int(user_id)}, config)
    if target != "private":
        raise ValueError("target must be 'private' or 'group'")
    return backend.api_request("send_poke", {"user_id": int(user_id)}, config)


def group_ban(group_id, user_id, duration: int = 600, config=None) -> dict[str, Any]:
    """Mute a group member for ``duration`` seconds."""
    return backend.api_request(
        "set_group_ban",
        {"group_id": int(group_id), "user_id": int(user_id), "duration": int(duration)},
        config,
    )


def group_whole_ban(group_id, enable: bool = True, config=None) -> dict[str, Any]:
    return backend.api_request(
        "set_group_whole_ban", {"group_id": int(group_id), "enable": enable}, config)


def group_kick(group_id, user_id, reject_rejoin: bool = False, config=None) -> dict[str, Any]:
    return backend.api_request(
        "set_group_kick",
        {"group_id": int(group_id), "user_id": int(user_id),
         "reject_add_request": reject_rejoin},
        config,
    )


def group_card(group_id, user_id, card: str = "", config=None) -> dict[str, Any]:
    return backend.api_request(
        "set_group_card",
        {"group_id": int(group_id), "user_id": int(user_id), "card": card},
        config,
    )


def group_admin(group_id, user_id, enable: bool = True, config=None) -> dict[str, Any]:
    return backend.api_request(
        "set_group_admin",
        {"group_id": int(group_id), "user_id": int(user_id), "enable": enable},
        config,
    )


def group_title(group_id, user_id, title: str, config=None) -> dict[str, Any]:
    return backend.api_request(
        "set_group_special_title",
        {"group_id": int(group_id), "user_id": int(user_id), "special_title": title},
        config,
    )


def group_info(group_id, config=None) -> dict[str, Any]:
    return backend.api_request("get_group_info", {"group_id": int(group_id)}, config)


def group_essence(group_id, config=None) -> list[dict[str, Any]]:
    data = backend.api_request("get_essence_msg_list", {"group_id": int(group_id)}, config)
    return data if isinstance(data, list) else []


def group_at_all_remain(group_id, config=None) -> dict[str, Any]:
    return backend.api_request("get_group_at_all_remain", {"group_id": int(group_id)}, config)


def group_sign(group_id, config=None) -> dict[str, Any]:
    return backend.api_request("send_group_sign", {"group_id": int(group_id)}, config)


def upload_file(target: str, peer_id, file_path: str, config=None) -> dict[str, Any]:
    """Upload a local file to a private chat or a group."""
    if target not in ("private", "group"):
        raise ValueError("target must be 'private' or 'group'")
    action = "upload_private_file" if target == "private" else "upload_group_file"
    key = "user_id" if target == "private" else "group_id"
    name = os.path.basename(file_path)
    return backend.api_request(
        action, {key: int(peer_id), "file": file_path, "name": name}, config)


def set_profile(nickname: str | None = None, signature: str | None = None,
                config=None) -> dict[str, Any]:
    """Update the account's nickname and/or signature."""
    params: dict[str, Any] = {}
    if nickname is not None:
        params["nickname"] = nickname
    if signature is not None:
        params["personal_signature"] = signature
    if not params:
        raise ValueError("nothing to update: pass --nickname and/or --signature")
    return backend.api_request("set_qq_profile", params, config)


def handle_request(flag: str, kind: str = "friend", approve: bool = True,
                   remark: str = "", config=None) -> dict[str, Any]:
    """Accept/deny a friend or group join request (flag comes from events)."""
    if kind not in ("friend", "group"):
        raise ValueError("kind must be 'friend' or 'group'")
    action = "set_friend_add_request" if kind == "friend" else "set_group_add_request"
    params: dict[str, Any] = {"flag": flag, "approve": approve}
    if kind == "friend" and remark:
        params["remark"] = remark
    return backend.api_request(action, params, config)


# ── Contacts (v2: replace the GUI's contact panel) ─────────────────────

def list_friends(config: backend.BackendConfig | None = None) -> list[dict[str, Any]]:
    """Compact friend list: id, nickname, remark."""
    return [
        {
            "user_id": f.get("user_id"),
            "nickname": f.get("nickname"),
            "remark": f.get("remark") or None,
        }
        for f in backend.get_friend_list(config)
    ]


def list_groups(config: backend.BackendConfig | None = None) -> list[dict[str, Any]]:
    """Compact group list: id, name, member count."""
    return [
        {
            "group_id": g.get("group_id"),
            "group_name": g.get("group_name"),
            "member_count": g.get("member_count"),
        }
        for g in backend.get_group_list(config)
    ]


def list_group_members(
    group_id: int | str,
    config: backend.BackendConfig | None = None,
) -> list[dict[str, Any]]:
    """Compact member list for a group."""
    members = backend.get_group_member_list(group_id, config)
    return [
        {
            "user_id": m.get("user_id"),
            "nickname": m.get("nickname"),
            "card": m.get("card") or None,
            "role": m.get("role"),
        }
        for m in members
    ]


# ── Message reading (v2: replace the GUI's chat panel) ─────────────────

_SEGMENT_SUMMARY = {
    "face": lambda d: f"[表情:{d.get('id', '?')}]",
    "at": lambda d: f"@{d.get('qq', '?')}",
    "image": lambda d: "[图片]",
    "record": lambda d: "[语音]",
    "video": lambda d: "[视频]",
    "file": lambda d: f"[文件:{d.get('name', '')}]",
    "reply": lambda d: f"[回复:{d.get('id', '?')}]",
    "forward": lambda d: "[合并转发]",
    "json": lambda d: "[卡片]",
    "xml": lambda d: "[卡片]",
}


def segments_to_text(message: Any) -> str:
    """Flatten an OneBot message (string or segment list) to readable text."""
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return str(message)
    parts: list[str] = []
    for seg in message:
        if isinstance(seg, str):
            parts.append(seg)
            continue
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type", "")
        data = seg.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        else:
            render = _SEGMENT_SUMMARY.get(seg_type)
            parts.append(render(data) if render else f"[{seg_type}]")
    return "".join(parts).strip()


def _compact_history_entry(entry: dict[str, Any]) -> dict[str, Any]:
    time_raw = entry.get("time")
    stamp = ""
    if isinstance(time_raw, (int, float)) and time_raw > 0:
        import datetime as _dt
        stamp = _dt.datetime.fromtimestamp(time_raw, _dt.timezone.utc).astimezone().strftime("%m-%d %H:%M")
    sender = entry.get("sender") or {}
    sender_id = entry.get("user_id") or sender.get("user_id")
    return {
        "time": stamp or time_raw,
        "sender_id": sender_id,
        "sender": sender.get("card") or sender.get("nickname") or (
            str(sender_id) if sender_id is not None else "?"
        ),
        "message_id": entry.get("message_id"),
        "text": segments_to_text(entry.get("message")),
    }


def read_history(
    target: str,
    peer_id: int | str,
    count: int = 20,
    config: backend.BackendConfig | None = None,
) -> dict[str, Any]:
    """Fetch recent chat history with a peer, in a compact readable shape.

    ``target`` is ``"private"`` or ``"group"``. Entries are ordered oldest-first
    when the backend reports them newest-first (history actions return the most
    recent batch, reversed for reading).
    """
    if target == "private":
        raw = backend.get_friend_msg_history(peer_id, count, config)
    elif target == "group":
        raw = backend.get_group_msg_history(peer_id, count, config)
    else:
        raise ValueError("target must be 'private' or 'group'")

    entries = [_compact_history_entry(e) for e in raw if isinstance(e, dict)]
    # OneBot history actions return newest-first; reading order is oldest-first.
    if len(entries) >= 2:
        def _key(e: dict[str, Any]) -> Any:
            t = e.get("time")
            return t if isinstance(t, (int, float)) else 0
        if _key(entries[0]) > _key(entries[-1]):
            entries.reverse()
    return {"target": target, "peer_id": int(peer_id), "count": len(entries),
            "messages": entries}

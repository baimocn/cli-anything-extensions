"""WeChat 4.x control surface for the harness — in-process library boundary.

This is the single module that owns ALL contact with the ``wechatauto``
library (https://github.com/fanyuantaier/wechatauto-replica), the only
community control surface verified against WeChat PC 4.2.0.x:

    * READ  — local SQLCipher 4 database decryption (WeChatDB): sessions,
      messages, contacts, groups, history export. Deterministic, no UI.
    * SEND  — UIA + OCR hybrid driving of the logged-in client (WeChat):
      send text/files, recall, poke, open chats. Needs a visible window.

Design notes (first principles):
    * No HTTP server, no daemon — wechatauto is imported in-process. The
      boundary here mirrors qq_backend.py: one module owns the foreign
      surface, everything returns plain dicts/lists, and foreign exceptions
      are normalized to RuntimeError so the CLI layer stays library-blind.
    * ``wechatauto`` is imported lazily. Without it installed the import
      raises a RuntimeError with an install hint; tests mock this module and
      never need the real library.
    * Window management is explicit: the client window must be visible for
      UIA clicks to land. Synthetic clicks are stolen by any window stacked
      above WeChat (verified live: an always-on-top assistant UI swallowed
      every click), so every GUI operation runs inside :func:`window_session`,
      which restores the window from the tray and makes it topmost for the
      duration, then reverts.

Environment variables (used only by :func:`resolve_config`):
    WX_DB_DIR             Override the WeChat data directory (auto-detected)
    WX_ACCOUNT            Pick an account subdirectory (e.g. ``wxid_xxx_c266``)
    WX_WINDOW_MANAGEMENT  Set ``0`` to disable tray-restore/topmost handling
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass
from typing import Any, Iterator

DEFAULT_WINDOW_CLASS_GTK = "Qt51514QWindowIcon"  # gate off (client default)
DEFAULT_WINDOW_CLASS_MMUI = "mmui::MainWindow"  # gate on (UIA tree live)
WINDOW_TITLE = "微信"

_WINDOW_MANAGE_ENV = "WX_WINDOW_MANAGEMENT"
_HIDE_AFTER_ENV = "WX_HIDE_AFTER_SEND"


@dataclass
class BackendConfig:
    """Settings for the wechatauto control surface."""

    db_dir: str | None = None
    account: str | None = None
    window_manage: bool = True
    hide_after: bool = False


def resolve_config(
    db_dir: str | None = None,
    account: str | None = None,
    window_manage: bool | None = None,
    hide_after: bool | None = None,
) -> BackendConfig:
    """Resolve backend config from explicit flags, then env, then defaults."""
    return BackendConfig(
        db_dir=(db_dir or os.environ.get("WX_DB_DIR") or None),
        account=(account or os.environ.get("WX_ACCOUNT") or None),
        window_manage=(
            window_manage
            if window_manage is not None
            else os.environ.get(_WINDOW_MANAGE_ENV, "1") not in ("0", "false", "no")
        ),
        hide_after=(
            hide_after
            if hide_after is not None
            else os.environ.get(_HIDE_AFTER_ENV, "0") in ("1", "true", "yes")
        ),
    )


def _import_wechatauto():
    """Import wechatauto, converting ImportError into an actionable error."""
    try:
        import wechatauto  # noqa: PLC0415 — deliberate lazy boundary import
    except ImportError as exc:
        raise RuntimeError(
            "wechatauto is not installed. Install the real-machine extras: "
            "pip install 'cli-anything-wx[real]' "
            "(wechatauto-replica + comtypes + pywin32, Windows only)"
        ) from exc
    return wechatauto


def _guard(fn):
    """Normalize foreign exceptions into RuntimeError for the CLI layer."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (RuntimeError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError(f"wx backend failure: {type(exc).__name__}: {exc}") from exc

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ── WeChatDB instance cache (key extraction is expensive) ───────────────

_DB_CACHE: dict[tuple, Any] = {}


def reset_backend_cache() -> None:
    """Drop cached WeChatDB/WeChat instances (used by tests)."""
    _DB_CACHE.clear()


def _db(config: BackendConfig | None = None):
    cfg = config or resolve_config()
    key = (cfg.db_dir, cfg.account)
    if key not in _DB_CACHE:
        wx = _import_wechatauto()
        _DB_CACHE[key] = wx.WeChatDB(db_dir=cfg.db_dir, account=cfg.account)
    return _DB_CACHE[key]


# ── Window management (the UIA prerequisite) ────────────────────────────

def _find_main_window():
    """Return the WeChat main-window hwnd, or None when not visible/existing."""
    import win32gui  # noqa: PLC0415 — Windows-only, imported lazily

    for cls in (DEFAULT_WINDOW_CLASS_MMUI, DEFAULT_WINDOW_CLASS_GTK):
        hwnd = win32gui.FindWindow(cls, WINDOW_TITLE)
        if hwnd:
            return hwnd
    # Fall back to a title scan (client updates may rename the class).
    found = []

    def _enum(h, _acc):
        try:
            if win32gui.GetWindowText(h) == WINDOW_TITLE and (
                "Qt" in win32gui.GetClassName(h) or "mmui" in win32gui.GetClassName(h)
            ):
                found.append(h)
        except Exception:
            pass

    win32gui.EnumWindows(_enum, None)
    return found[0] if found else None


@contextlib.contextmanager
def window_session(config: BackendConfig | None = None) -> Iterator[int]:
    """Bring the WeChat main window to a clickable state for the duration.

    Restores it from the tray if hidden, makes it topmost so overlapping
    windows cannot steal synthetic clicks, and reverts on exit: topmost is
    removed and the previously-foreground window gets the focus back, so a
    send only interrupts the desktop for a few seconds. Why focus is needed
    at all (verified live): WeChat 4.x draws with Qt and its accessibility
    tree **collapses to two bare panes the moment the window loses focus** —
    there is no background-UIA surface to drive, so every send must briefly
    activate the window.

    With ``hide_after`` the window is additionally minimized back to the
    tray afterwards.

    Yields the hwnd. Disabled entirely by ``WX_WINDOW_MANAGEMENT=0`` (yields
    -1 without touching the window) for headless/CI environments.
    """
    cfg = config or resolve_config()
    if not cfg.window_manage:
        yield -1
        return
    import win32con  # noqa: PLC0415
    import win32gui  # noqa: PLC0415

    hwnd = _find_main_window()
    if not hwnd:
        raise RuntimeError(
            "WeChat main window not found. Start the WeChat PC 4.x client, "
            "log in, and make sure it is running (not just the tray icon)."
        )
    was_topmost = False
    previous = None
    try:
        if not win32gui.IsWindowVisible(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.8)
        # HWND_TOPMOST: -1 = not topmost. Insert after so it takes effect.
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
        )
        was_topmost = True
        # Remember whose focus we borrowed so it can be handed back below.
        with contextlib.suppress(Exception):
            previous = win32gui.GetForegroundWindow()
        if previous != hwnd:
            with contextlib.suppress(Exception):
                win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        yield hwnd
    finally:
        if was_topmost:
            with contextlib.suppress(Exception):
                fresh = _find_main_window() or hwnd
                win32gui.SetWindowPos(
                    fresh, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
                )
            if cfg.hide_after:
                with contextlib.suppress(Exception):
                    win32gui.ShowWindow(fresh, win32con.SW_MINIMIZE)
        # hand the focus back to whoever had it before the operation
        if previous is not None and previous != hwnd:
            with contextlib.suppress(Exception):
                win32gui.SetForegroundWindow(previous)


def window_status(config: BackendConfig | None = None) -> dict[str, Any]:
    """Probe the client window without modifying it (for ``status``)."""
    cfg = config or resolve_config()
    info: dict[str, Any] = {"window_manage": cfg.window_manage}
    try:
        import win32gui  # noqa: PLC0415
    except ImportError:
        info["available"] = False
        info["reason"] = "pywin32 not installed"
        return info
    hwnd = _find_main_window()
    if not hwnd:
        info["available"] = False
        info["reason"] = "main window not found (client not running/logged in)"
        return info
    info["available"] = True
    info["hwnd"] = int(hwnd)
    info["visible"] = bool(win32gui.IsWindowVisible(hwnd))
    info["window_class"] = win32gui.GetClassName(hwnd)
    # mmui::MainWindow means the UIA tree is materialized (gate on).
    info["uia_tree_live"] = info["window_class"] == DEFAULT_WINDOW_CLASS_MMUI
    return info


# ── Response normalization ──────────────────────────────────────────────

def _response_to_dict(resp: Any) -> dict[str, Any]:
    """Flatten a WxResponse into a plain dict (it is dict-like)."""
    if resp is None:
        return {"status": "unknown", "message": "no response object returned"}
    if isinstance(resp, dict):
        return dict(resp)
    out: dict[str, Any] = {}
    for attr in ("status", "message", "data"):
        val = getattr(resp, attr, None)
        if val is None and isinstance(resp, dict):
            val = resp.get(attr)
        out[attr] = val
    return out


def _open_wechat(config: BackendConfig | None = None):
    wx = _import_wechatauto()
    cfg = config or resolve_config()
    if cfg.window_manage:
        # Constructed inside the caller's window_session; window is ready.
        return wx.WeChat()
    return wx.WeChat()


# ── READ path: decrypted local databases ────────────────────────────────

@_guard
def get_self_info(config: BackendConfig | None = None) -> dict[str, Any]:
    db = _db(config)
    info = dict(db.get_self_info() or {})
    info.setdefault("wxid", getattr(db, "wxid", None))
    return info


@_guard
def get_sessions(limit: int = 100, config: BackendConfig | None = None) -> list[dict[str, Any]]:
    return [dict(s) for s in _db(config).get_sessions(limit=limit) or []]


@_guard
def list_message_chats(config: BackendConfig | None = None) -> list[dict[str, Any]]:
    return [dict(c) for c in _db(config).list_message_chats() or []]


@_guard
def search_contacts(keyword: str, config: BackendConfig | None = None) -> list[dict[str, Any]]:
    return [dict(c) for c in _db(config).search_contact(keyword) or []]


@_guard
def get_groups(config: BackendConfig | None = None) -> list[dict[str, Any]]:
    return [dict(g) for g in _db(config).get_groups() or []]


@_guard
def get_group_members(group_id: str, config: BackendConfig | None = None) -> list[dict[str, Any]]:
    return [dict(m) for m in _db(config).get_group_members(group_id) or []]


@_guard
def get_messages(
    user: str, limit: int = 20, offset: int = 0, config: BackendConfig | None = None
) -> list[dict[str, Any]]:
    return [dict(m) for m in _db(config).get_messages(user, limit=limit, offset=offset) or []]


@_guard
def get_new_messages(
    user: str, since_seq: int = 0, limit: int = 200, config: BackendConfig | None = None
) -> list[dict[str, Any]]:
    return [
        dict(m)
        for m in _db(config).get_new_messages(user, since_seq=since_seq, limit=limit) or []
    ]


@_guard
def export_history(
    out_path: str,
    users: list[str] | None = None,
    limit_per_chat: int | None = None,
    fmt: str = "json",
    config: BackendConfig | None = None,
) -> dict[str, Any]:
    """Export chat history to ``out_path`` (JSON or SQLite via the library)."""
    result = _db(config).export_history(
        out_path, fmt=fmt, users=users, limit_per_chat=limit_per_chat
    )
    return dict(result) if isinstance(result, dict) else {"result": str(result)}


@_guard
def list_labels(config: BackendConfig | None = None) -> list[dict[str, Any]]:
    return [dict(l) for l in _db(config).list_labels() or []]


@_guard
def db_passthrough(method: str, kwargs: dict[str, Any],
                   config: BackendConfig | None = None) -> Any:
    """Read-only passthrough onto the WeChatDB method surface.

    Only whitelisted read methods are allowed — this is the analog of the QQ
    harness's ``call`` escape hatch, minus anything that mutates state.
    """
    allowed = {
        "get_self_info", "get_sessions", "list_message_chats", "search_contact",
        "get_groups", "get_group_members", "get_messages", "get_new_messages",
        "get_message_row", "get_nickname", "username_by_nickname",
        "group_name_to_id", "group_id_to_name", "nickname_map", "list_labels",
        "label_names", "list_message_chats", "get_image_rows", "get_voice_rows",
        "sender_username", "search_contact",
    }
    if method not in allowed:
        raise ValueError(
            f"method {method!r} is not in the read-only whitelist; "
            f"allowed: {', '.join(sorted(allowed))}"
        )
    db = _db(config)
    result = getattr(db, method)(**kwargs)
    if isinstance(result, (list, tuple)):
        return [dict(r) if isinstance(r, dict) else r for r in result]
    if isinstance(result, dict):
        return dict(result)
    return result


# ── Chat-key resolution (display name → message-DB username) ────────────

@_guard
def resolve_chat_key(who: str, config: BackendConfig | None = None) -> dict[str, Any]:
    """Resolve a display name / wxid / filehelper into the message-DB key.

    WeChat 4.x stores ``filehelper`` (文件传输助手) traffic under the account's
    own wxid, so resolution returns ordered candidates for the read layer to
    try: [exact, nickname-hit, group-name-hit, self-wxid (filehelper/self)].
    """
    db = _db(config)
    candidates: list[str] = []
    if who:
        candidates.append(who)
    nickname_hit = db.username_by_nickname(who)
    if nickname_hit and nickname_hit not in candidates:
        candidates.append(nickname_hit)
    group_hit = db.group_name_to_id(who)
    if group_hit and group_hit not in candidates:
        candidates.append(group_hit)
    self_wxid = getattr(db, "wxid", None)
    if who in ("filehelper", "文件传输助手") or who == self_wxid:
        if self_wxid and self_wxid not in candidates:
            candidates.append(self_wxid)
    return {"query": who, "candidates": candidates, "self_wxid": self_wxid}


@_guard
def find_in_db(
    who: str,
    content: str,
    timeout: float = 8.0,
    config: BackendConfig | None = None,
) -> dict[str, Any]:
    """Poll the message DB for a row whose content matches exactly.

    Used to verify a UIA send actually landed (the WeChat equivalent of the
    QQ harness's get_msg read-back). Returns ``{"found": row-or-None,
    "key": the chat key that matched, "elapsed_s": ...}``.
    """
    deadline = time.monotonic() + timeout
    resolution = resolve_chat_key(who, config)
    candidates = resolution["candidates"] or [who]
    started = time.monotonic()
    while True:
        for key in candidates:
            try:
                rows = get_messages(key, limit=10, config=config)
            except RuntimeError:
                continue
            for row in rows:
                if str(row.get("content", "")) == content:
                    return {
                        "found": row,
                        "key": key,
                        "elapsed_s": round(time.monotonic() - started, 2),
                    }
        if time.monotonic() >= deadline:
            return {"found": None, "key": None, "elapsed_s": timeout}
        time.sleep(0.5)


# ── SEND path: UIA driving of the logged-in client ──────────────────────

@_guard
def send_text(
    text: str,
    who: str | None = None,
    verify: bool = False,
    config: BackendConfig | None = None,
) -> dict[str, Any]:
    """Send a text message through the real client (UIA).

    ``verify=True`` uses the library's ``quick_send`` read-back gate (it
    confirms the composed content before pressing Enter and re-checks after).
    """
    wx = _import_wechatauto()
    cfg = config or resolve_config()
    with window_session(cfg):
        if verify:
            resp = wx.quick_send(text, who, verify=True)
        else:
            client = _open_wechat(cfg)
            resp = client.SendMsg(text, who) if who else client.SendMsg(text)
    return _response_to_dict(resp)


@_guard
def send_file(
    file_path: str,
    who: str | None = None,
    config: BackendConfig | None = None,
) -> dict[str, Any]:
    """Send a local file through the real client (UIA)."""
    cfg = config or resolve_config()
    wx = _import_wechatauto()
    with window_session(cfg):
        client = _open_wechat(cfg)
        if who:
            resp = client.SendFiles(file_path, who)
        else:
            resp = client.SendFiles(file_path)
    return _response_to_dict(resp)


@_guard
def recall_last(who: str | None = None, config: BackendConfig | None = None) -> dict[str, Any]:
    """Recall the last message this account sent in a chat (UIA)."""
    cfg = config or resolve_config()
    wx = _import_wechatauto()
    with window_session(cfg):
        client = _open_wechat(cfg)
        resp = client.RecallLastMessage(who) if who else client.RecallLastMessage()
    return _response_to_dict(resp)


@_guard
def poke(who: str | None = None, config: BackendConfig | None = None) -> dict[str, Any]:
    """Poke (拍一拍) a chat member (UIA)."""
    cfg = config or resolve_config()
    wx = _import_wechatauto()
    with window_session(cfg):
        client = _open_wechat(cfg)
        resp = client.Poke(who) if who else client.Poke()
    return _response_to_dict(resp)


@_guard
def chat_open(who: str, config: BackendConfig | None = None) -> dict[str, Any]:
    """Open a chat window and bring it to front (UIA)."""
    cfg = config or resolve_config()
    wx = _import_wechatauto()
    with window_session(cfg):
        client = _open_wechat(cfg)
        client.ChatWith(who)
        client.Show()
    return {"opened": who}


@_guard
def ui_messages(who: str, limit: int = 10, config: BackendConfig | None = None) -> dict[str, Any]:
    """Read messages of the *open* chat via the UIA tree (client-side view).

    Complements the DB read path: the DB only has what is committed to
    disk; the UI tree shows exactly what the client window renders.
    """
    cfg = config or resolve_config()
    wx = _import_wechatauto()
    with window_session(cfg):
        client = _open_wechat(cfg)
        client.ChatWith(who)
        messages = client.GetAllMessage()
    out = []
    for m in list(messages or [])[-limit:]:
        out.append(
            {
                "sender": getattr(m, "sender", None) or (m.get("sender") if isinstance(m, dict) else None),
                "content": getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None),
                "type": getattr(m, "type", None) or (m.get("type") if isinstance(m, dict) else None),
                "time": getattr(m, "time", None) or (m.get("time") if isinstance(m, dict) else None),
            }
        )
    return {"who": who, "count": len(out), "messages": out}

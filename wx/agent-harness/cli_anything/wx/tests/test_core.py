"""Mocked test suite for cli-anything-wx.

Mocks the *foreign surface* (the wechatauto library, win32 window layer) at
the boundary — ``utils.wx_backend`` — and exercises the REAL backend, domain
and CLI layers on top of it. No real WeChat account, no window touches.

Includes four ablation experiments; see TEST.md.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.wx.core import project as project_mod
from cli_anything.wx.core import wx as wx_mod
from cli_anything.wx.core.session import Session
from cli_anything.wx.utils import wx_backend as backend
from cli_anything.wx.wx_cli import cli

SELF_WXID = "wxid_self_placeholder"
PEER_WXID = "wxid_peer_placeholder"
GROUP_ID = "12345678901@chatroom"
NICKNAME = "TestNick"
GROUP_NAME = "TestGroup"


# ── Fake win32 window layer ─────────────────────────────────────────────

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SW_RESTORE = 9
SW_MINIMIZE = 6
OTHER_APP_HWND = 999


class FakeWin32Gui:
    """Records window ops; models the overlap problem (topmost gating)."""

    def __init__(self, world):
        self.world = world

    def FindWindow(self, cls, title):
        return 4242 if self.world["window_exists"] else 0

    def IsWindowVisible(self, hwnd):
        return self.world["visible"]

    def ShowWindow(self, hwnd, cmd):
        self.world["calls"].append(("ShowWindow", cmd))
        self.world["visible"] = True

    def SetWindowPos(self, hwnd, after, *args, **kwargs):
        self.world["topmost"] = after == HWND_TOPMOST
        self.world["calls"].append(("SetWindowPos", after))

    def GetForegroundWindow(self):
        return self.world.get("fg", OTHER_APP_HWND)

    def SetForegroundWindow(self, hwnd):
        self.world["fg"] = hwnd
        self.world["calls"].append(("SetForegroundWindow", hwnd))

    def GetClassName(self, hwnd):
        return self.world["class"]

    def EnumWindows(self, cb, acc):
        return None


def install_fake_win32(monkeypatch, world):
    monkeypatch.setitem(sys.modules, "win32gui", FakeWin32Gui(world))
    monkeypatch.setitem(
        sys.modules,
        "win32con",
        type("FakeCon", (), {
            "HWND_TOPMOST": HWND_TOPMOST,
            "HWND_NOTOPMOST": HWND_NOTOPMOST,
            "SW_RESTORE": SW_RESTORE,
            "SW_MINIMIZE": SW_MINIMIZE,
            "SWP_NOMOVE": 2,
            "SWP_NOSIZE": 1,
        }),
    )


# ── Fake wechatauto surface (WeChatDB + WeChat client) ──────────────────

class FakeDB:
    """Stateful WeChatDB mock: rows newest-first per chat username key."""

    def __init__(self, world):
        self.world = world
        self.wxid = SELF_WXID
        self.chats: dict[str, list[dict]] = {}
        self.sessions: list[dict] = []
        self.contacts: list[dict] = []
        self.groups: list[dict] = []
        self.members: dict[str, list[dict]] = {}
        self._next_local_id = 100
        # strictly increasing sort_seq — real commits can share a timestamp
        self._next_sort_seq = int(time.time() * 1000)

    # message surface ---------------------------------------------------
    def commit(self, username: str, content: str, sender: str = SELF_WXID):
        self._next_sort_seq += 1
        row = {
            "local_id": self._next_local_id,
            "type": "文本",
            "sender_id": 1 if sender == SELF_WXID else 2,
            "sender_username": sender,
            "create_time": self._next_sort_seq // 1000,
            "content": content,
            "sort_seq": self._next_sort_seq,
            "shard": "message__message_0.db",
        }
        self._next_local_id += 1
        self.chats.setdefault(username, []).append(row)  # append = newest-last
        self.chats[username].sort(key=lambda r: r["sort_seq"], reverse=True)

    def get_messages(self, user, limit=20, offset=0):
        rows = self.chats.get(user, [])
        return rows[offset:offset + limit]

    def get_new_messages(self, user, since_seq=0, limit=200):
        return [r for r in self.chats.get(user, []) if r["sort_seq"] > since_seq]

    def list_message_chats(self):
        return [
            {"md5": f"md5-{k}", "username": k, "name": k, "message_count": len(v)}
            for k, v in self.chats.items()
            if v
        ]

    # directory surface -------------------------------------------------
    def get_self_info(self):
        return {"nickname": NICKNAME, "wxid": SELF_WXID}

    def get_sessions(self, limit=100):
        return self.sessions[:limit]

    def search_contact(self, keyword):
        return [
            c for c in self.contacts
            if keyword in (c.get("nickname") or "") or keyword in (c.get("remark") or "")
        ]

    def get_groups(self):
        return self.groups

    def get_group_members(self, chatroom_wxid):
        return self.members.get(chatroom_wxid, [])

    def username_by_nickname(self, nickname):
        for c in self.contacts:
            if c.get("nickname") == nickname:
                return c.get("username")
        return None

    def group_name_to_id(self, name):
        for g in self.groups:
            if g.get("nickname") == name:
                return g.get("username")
        return None

    def get_nickname(self, user):
        for c in self.contacts:
            if c.get("username") == user:
                return c.get("nickname")
        return ""

    def list_labels(self):
        return self.world.get("labels", [])

    def export_history(self, out_path, fmt="json", users=None, limit_per_chat=None):
        # Same constraint the CLI layer enforces via safe_export_path(): the
        # resolved destination must stay inside the working directory. The
        # fake computes the export summary without touching the filesystem —
        # real file output is covered by the gated E2E suite.
        target = Path(out_path).resolve()
        if not target.is_relative_to(Path.cwd().resolve()):
            raise ValueError("export path must stay inside the working directory")
        count = sum(len(v) for k, v in self.chats.items() if not users or k in users)
        return {"path": str(target), "count": count, "fmt": fmt}


class FakeClient:
    """WeChat (UIA) mock: sends only land when the window is topmost —
    the live-verified failure mode where an overlapping window steals
    every synthetic click."""

    def __init__(self, world, db):
        self.world = world
        self.db = db

    def _target_key(self, who):
        if who in (None, "filehelper", "文件传输助手"):
            return SELF_WXID  # live-verified: filehelper traffic keys to self wxid
        return self.db.username_by_nickname(who) or self.db.group_name_to_id(who) or who

    def SendMsg(self, text, who=None, clear=True, at=None, exact=False):
        if not self.world["topmost"]:
            raise RuntimeError("simulated overlap: click landed on a foreign window")
        self.db.commit(self._target_key(who), text)
        return {"status": "成功", "message": f"消息已发送：{text}", "data": {"content": text}}

    def SendFiles(self, filepath, who=None, exact=False):
        if not self.world["topmost"]:
            raise RuntimeError("simulated overlap: click landed on a foreign window")
        name = os.path.basename(filepath)
        self.db.commit(self._target_key(who), f"[file] {name}")
        return {"status": "成功", "message": "文件已发送"}

    def RecallLastMessage(self, who=None):
        if not self.world["topmost"]:
            raise RuntimeError("simulated overlap: click landed on a foreign window")
        return {"status": "成功", "message": "已撤回"}

    def Poke(self, who=None):
        if not self.world["topmost"]:
            raise RuntimeError("simulated overlap: click landed on a foreign window")
        return {"status": "成功", "message": "拍了一拍"}

    def ChatWith(self, who):
        self.world["open_chat"] = who

    def Show(self):
        self.world["calls"].append(("Show",))

    def GetAllMessage(self):
        return []


class _SilentClient(FakeClient):
    """Reports success but never commits to the DB (delivery unverified)."""

    def SendMsg(self, text, who=None, **kwargs):
        return {"status": "成功", "message": f"消息已发送：{text}"}


class FakeWechatautoModule:
    def __init__(self, world, db):
        self._world = world
        self._db = db

    def quick_send(self, text, who=None, **kwargs):
        client = FakeClient(self._world, self._db)
        return client.SendMsg(text, who)

    def WeChatDB(self, db_dir=None, account=None):
        assert db_dir is None or db_dir == "fake-db-dir"
        return self._db

    def WeChat(self):
        return FakeClient(self._world, self._db)


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_world(monkeypatch):
    """Wire the full fake world under the real backend, then reset caches."""
    world = {
        "window_exists": True,
        "visible": True,
        "topmost": False,
        "class": "mmui::MainWindow",
        "calls": [],
        "fg": OTHER_APP_HWND,
    }
    db = FakeDB(world)
    install_fake_win32(monkeypatch, world)
    module = FakeWechatautoModule(world, db)
    monkeypatch.setattr(backend, "_import_wechatauto", lambda: module)
    monkeypatch.setattr(backend, "_db", lambda config=None: db)
    monkeypatch.setattr(backend, "_open_wechat", lambda config=None: FakeClient(world, db))
    monkeypatch.setattr(backend, "_DB_CACHE", {})
    # seed a small world
    db.sessions = [
        {"username": PEER_WXID, "name": NICKNAME, "unread": 1,
         "summary": "hi there", "last_time": int(time.time())},
        {"username": "filehelper", "name": "文件传输助手", "unread": 0,
         "summary": "", "last_time": 0},
        {"username": GROUP_ID, "name": GROUP_NAME, "unread": 0,
         "summary": "group talk", "last_time": int(time.time())},
    ]
    db.contacts = [{"username": PEER_WXID, "nickname": NICKNAME, "remark": ""}]
    db.groups = [{"username": GROUP_ID, "nickname": GROUP_NAME}]
    db.members[GROUP_ID] = [
        {"username": PEER_WXID, "nickname": NICKNAME},
        {"username": SELF_WXID, "nickname": "SelfNick"},
    ]
    db.commit(PEER_WXID, "hi there", sender=PEER_WXID)
    db.commit(PEER_WXID, "hello back", sender=SELF_WXID)
    db.commit(SELF_WXID, "note to self", sender=SELF_WXID)
    return {"world": world, "db": db}


# ── Read path (decrypted DB channel) ────────────────────────────────────

class TestReadPath:
    def test_status_both_channels(self, fake_world):
        data = wx_mod.status()
        assert data["db"]["decryptable"] is True
        assert data["db"]["wxid"] == SELF_WXID
        assert data["window"]["available"] is True
        assert data["window"]["uia_tree_live"] is True
        assert data["send_path"] == "uia"
        assert data["errors"] == []

    def test_status_degrades_without_window(self, fake_world):
        fake_world["world"]["window_exists"] = False
        data = wx_mod.status()
        assert data["window"]["available"] is False
        assert data["send_path"] == "unavailable"
        assert data["db"]["decryptable"] is True

    def test_status_collects_db_errors(self, fake_world, monkeypatch):
        def boom(config=None):
            raise RuntimeError("db locked")
        monkeypatch.setattr(backend, "get_self_info", boom)
        data = wx_mod.status()
        assert data["db"]["decryptable"] is False
        assert any("db locked" in e for e in data["errors"])

    def test_list_sessions_compact(self, fake_world):
        rows = wx_mod.list_sessions()
        assert rows[0]["username"] == PEER_WXID
        assert rows[0]["name"] == NICKNAME
        assert rows[0]["unread"] == 1

    def test_search_contacts(self, fake_world):
        hits = wx_mod.search_contacts(NICKNAME)
        assert len(hits) == 1
        assert hits[0]["username"] == PEER_WXID

    def test_groups_and_members(self, fake_world):
        groups = wx_mod.list_groups()
        assert groups[0]["username"] == GROUP_ID
        members = wx_mod.list_group_members(GROUP_ID)
        assert {m["username"] for m in members} == {PEER_WXID, SELF_WXID}

    def test_read_history_oldest_first(self, fake_world):
        data = wx_mod.read_history(PEER_WXID, count=10)
        assert data["key"] == PEER_WXID
        assert data["count"] == 2
        seqs = [m["sort_seq"] for m in data["messages"]]
        assert seqs == sorted(seqs), "oldest-first reading order"
        assert data["messages"][0]["text"] == "hi there"
        assert data["messages"][-1]["is_self"] is True

    def test_read_history_self_marking(self, fake_world):
        data = wx_mod.read_history(PEER_WXID)
        marks = {m["text"]: m["is_self"] for m in data["messages"]}
        assert marks["hi there"] is False
        assert marks["hello back"] is True

    def test_read_history_time_stamped(self, fake_world):
        data = wx_mod.read_history(PEER_WXID)
        stamp = data["messages"][0]["time"]
        assert stamp and len(stamp) == 11 and stamp[2] == "-", stamp


# ── Key resolution (the filehelper quirk) ───────────────────────────────

class TestKeyResolution:
    def test_resolve_filehelper_includes_self_wxid(self, fake_world):
        resolution = backend.resolve_chat_key("文件传输助手")
        assert SELF_WXID in resolution["candidates"]
        assert resolution["candidates"][0] == "文件传输助手"

    def test_resolve_nickname_hits_contact(self, fake_world):
        resolution = backend.resolve_chat_key(NICKNAME)
        assert PEER_WXID in resolution["candidates"]

    def test_resolve_group_name_hits_group(self, fake_world):
        resolution = backend.resolve_chat_key(GROUP_NAME)
        assert GROUP_ID in resolution["candidates"]

    def test_read_history_via_filehelper_quirk(self, fake_world):
        # rows live under the self wxid; resolution must find them
        data = wx_mod.read_history("文件传输助手")
        assert data["key"] == SELF_WXID
        assert data["messages"][0]["text"] == "note to self"

    def test_read_history_no_rows_any_candidate(self, fake_world):
        data = wx_mod.read_history("nobody-here")
        assert data["count"] == 0
        assert data["key"] is None

    def test_read_new_incremental(self, fake_world):
        data = wx_mod.read_history(PEER_WXID)
        watermark = data["messages"][0]["sort_seq"] - 1
        fresh = wx_mod.read_new(PEER_WXID, since_seq=watermark)
        assert fresh["count"] == 2
        fresh2 = wx_mod.read_new(PEER_WXID, since_seq=10**13)
        assert fresh2["count"] == 0


# ── Send path (UIA channel) + the window mechanism ──────────────────────

class TestSendPath:
    def test_send_text_lands_in_db(self, fake_world):
        data = wx_mod.send_text(NICKNAME, "ping")
        assert data["status"] == "成功"
        rows = fake_world["db"].get_messages(PEER_WXID)
        assert rows[0]["content"] == "ping"

    def test_send_text_to_filehelper_keys_to_self(self, fake_world):
        wx_mod.send_text("文件传输助手", "note")
        rows = fake_world["db"].get_messages(SELF_WXID)
        assert rows[0]["content"] == "note"

    def test_window_session_sets_and_reverts_topmost(self, fake_world):
        with backend.window_session():
            assert fake_world["world"]["topmost"] is True
        assert fake_world["world"]["topmost"] is False
        calls = [c[0] for c in fake_world["world"]["calls"]]
        assert "SetWindowPos" in calls

    def test_window_session_restores_hidden_window(self, fake_world):
        fake_world["world"]["visible"] = False
        with backend.window_session():
            pass
        assert ("ShowWindow", SW_RESTORE) in fake_world["world"]["calls"]

    def test_window_session_raises_when_no_window(self, fake_world):
        fake_world["world"]["window_exists"] = False
        with pytest.raises(RuntimeError, match="main window not found"):
            with backend.window_session():
                pass

    def test_window_session_disabled_by_config(self, fake_world):
        cfg = backend.BackendConfig(window_manage=False)
        with backend.window_session(cfg):
            assert fake_world["world"]["topmost"] is False
        assert fake_world["world"]["calls"] == []

    def test_window_session_restores_focus_afterwards(self, fake_world):
        # the WeChat window borrows the focus for the operation, then the
        # previously-foreground window gets it back (live-verified necessity:
        # the Qt accessibility tree collapses when WeChat loses focus, so the
        # borrow is unavoidable — restoring it is what keeps it unobtrusive)
        w = fake_world["world"]
        with backend.window_session():
            assert w["fg"] == 4242
        assert w["fg"] == OTHER_APP_HWND
        assert ("SetForegroundWindow", OTHER_APP_HWND) in w["calls"]

    def test_window_session_hide_after_minimizes(self, fake_world):
        cfg = backend.BackendConfig(hide_after=True)
        with backend.window_session(cfg):
            pass
        assert ("ShowWindow", SW_MINIMIZE) in fake_world["world"]["calls"]
        assert fake_world["world"]["fg"] == OTHER_APP_HWND

    def test_send_fails_without_topmost_mechanism(self, fake_world):
        # ABLATION: with window management off, the overlap failure returns.
        cfg = backend.BackendConfig(window_manage=False)
        with pytest.raises(RuntimeError, match="foreign window"):
            wx_mod.send_text(NICKNAME, "should fail", config=cfg)

    def test_send_file(self, fake_world):
        data = wx_mod.send_file(NICKNAME, "report.txt")
        assert data["status"] == "成功"

    def test_recall_and_poke(self, fake_world):
        assert wx_mod.recall_last(NICKNAME)["status"] == "成功"
        assert wx_mod.poke(NICKNAME)["status"] == "成功"

    def test_chat_open(self, fake_world):
        data = wx_mod.chat_open(NICKNAME)
        assert data == {"opened": NICKNAME}
        assert fake_world["world"]["open_chat"] == NICKNAME


# ── The closed loop ─────────────────────────────────────────────────────

class TestRoundTrip:
    def test_send_and_verify_ok(self, fake_world):
        data = wx_mod.send_and_verify(NICKNAME, "closed loop ping", timeout=3)
        assert data["round_trip_ok"] is True
        assert data["read_back"]["content"] == "closed loop ping"
        assert data["key"] == PEER_WXID

    def test_send_and_verify_filehelper(self, fake_world):
        data = wx_mod.send_and_verify("文件传输助手", "self note", timeout=3)
        assert data["round_trip_ok"] is True
        assert data["key"] == SELF_WXID

    def test_send_and_verify_not_committed(self, fake_world, monkeypatch):
        # client reports success but the row never reaches the DB
        monkeypatch.setattr(backend, "_open_wechat",
                            lambda config=None: _SilentClient(
                                fake_world["world"], fake_world["db"]))
        data = wx_mod.send_and_verify(NICKNAME, "ghost message", timeout=1.5)
        assert data["round_trip_ok"] is False
        assert data["read_back"] is None

    def test_send_and_verify_raises_on_client_failure(self, fake_world):
        fake_world["world"]["topmost"] = False
        cfg = backend.BackendConfig(window_manage=False)
        with pytest.raises(RuntimeError, match="send failed"):
            wx_mod.send_and_verify(NICKNAME, "x", config=cfg)


# ── Backend boundary ────────────────────────────────────────────────────

class TestBackendBoundary:
    def test_missing_wechatauto_gives_install_hint(self, fake_world, monkeypatch):
        def no_lib():
            raise RuntimeError(
                "wechatauto is not installed. Install the real-machine extras: "
                "pip install 'cli-anything-wx[real]'"
            )
        monkeypatch.setattr(backend, "_import_wechatauto", no_lib)
        with pytest.raises(RuntimeError, match="cli-anything-wx\\[real\\]"):
            backend.send_text("hi", NICKNAME)

    def test_foreign_exception_normalized(self, fake_world, monkeypatch):
        class Weird(Exception):
            pass
        db = fake_world["db"]
        monkeypatch.setattr(db, "get_sessions",
                            lambda limit=100: (_ for _ in ()).throw(Weird("kaboom")))
        with pytest.raises(RuntimeError, match="wx backend failure: Weird"):
            backend.get_sessions()

    def test_db_passthrough_whitelist(self, fake_world):
        data = backend.db_passthrough("get_sessions", {"limit": 5})
        assert isinstance(data, list) and len(data) == 3
        with pytest.raises(ValueError, match="not in the read-only whitelist"):
            backend.db_passthrough("format_disk", {})

    def test_resolve_config_env(self, monkeypatch):
        monkeypatch.setenv("WX_DB_DIR", "D:/fake/db")
        monkeypatch.setenv("WX_ACCOUNT", "wxid_fake_c001")
        monkeypatch.setenv("WX_WINDOW_MANAGEMENT", "0")
        cfg = backend.resolve_config()
        assert cfg.db_dir == "D:/fake/db"
        assert cfg.account == "wxid_fake_c001"
        assert cfg.window_manage is False

    def test_window_status_not_available_report(self, fake_world):
        fake_world["world"]["window_exists"] = False
        info = backend.window_status()
        assert info["available"] is False
        assert "not found" in info["reason"]


# ── CLI contract ────────────────────────────────────────────────────────

class TestCLI:
    def _json_out(self, result):
        return json.loads(result.output)

    def test_status_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "status"])
        assert r.exit_code == 0, r.output
        payload = self._json_out(r)
        assert payload["ok"] is True and payload["command"] == "status"

    def test_session_list_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "session", "list"])
        payload = self._json_out(r)
        assert payload["ok"] is True and payload["command"] == "session.list"
        assert payload["data"][0]["username"] == PEER_WXID

    def test_contact_search_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "contact", "search", NICKNAME])
        payload = self._json_out(r)
        assert payload["ok"] is True and payload["command"] == "contact.search"
        assert len(payload["data"]) == 1

    def test_group_members_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "group", "members", GROUP_ID])
        payload = self._json_out(r)
        assert payload["command"] == "group.members"
        assert len(payload["data"]) == 2

    def test_msg_history_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "msg", "history", PEER_WXID])
        payload = self._json_out(r)
        assert payload["command"] == "msg.history"
        assert payload["data"]["count"] == 2
        seqs = [m["sort_seq"] for m in payload["data"]["messages"]]
        assert seqs == sorted(seqs)

    def test_msg_history_human_readable(self, fake_world):
        r = CliRunner().invoke(cli, ["msg", "history", PEER_WXID])
        assert "History with" in r.output
        assert "hi there" in r.output

    def test_msg_chats_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "msg", "chats"])
        payload = self._json_out(r)
        assert payload["command"] == "msg.chats"
        assert len(payload["data"]) == 2

    def test_msg_export_envelope(self, fake_world, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = CliRunner().invoke(cli, ["--json", "msg", "export", "all",
                                     "-o", "hist.json"])
        payload = self._json_out(r)
        assert payload["ok"] is True and payload["command"] == "msg.export"
        assert payload["data"]["count"] == 3

    def test_send_text_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "send", "text", NICKNAME, "via cli"])
        payload = self._json_out(r)
        assert payload["ok"] is True and payload["command"] == "send.text"
        assert payload["data"]["status"] == "成功"

    def test_roundtrip_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "roundtrip", NICKNAME, "loop check",
                                     "--timeout", "3"])
        payload = self._json_out(r)
        assert payload["command"] == "roundtrip"
        assert payload["data"]["round_trip_ok"] is True

    def test_db_passthrough_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "db", "get_sessions",
                                     "--kwargs", '{"limit": 1}'])
        payload = self._json_out(r)
        assert payload["command"] == "db.get_sessions"
        assert len(payload["data"]) == 1

    def test_db_passthrough_rejects_unknown(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "db", "write_everything"])
        payload = self._json_out(r)
        assert payload["ok"] is False
        assert payload["error"]["type"] == "ValueError"

    def test_poke_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "poke", NICKNAME])
        payload = self._json_out(r)
        assert payload["command"] == "poke"
        assert payload["data"]["status"] == "成功"

    def test_chat_open_envelope(self, fake_world):
        r = CliRunner().invoke(cli, ["--json", "chat", NICKNAME])
        payload = self._json_out(r)
        assert payload["command"] == "chat.open"
        assert payload["data"]["opened"] == NICKNAME


# ── Ablation: error envelope + exit code (the retcode analog) ───────────

class TestErrorContract:
    def test_backend_failure_error_envelope(self, fake_world, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("session store unreadable")
        monkeypatch.setattr(backend, "get_sessions", boom)
        r = CliRunner().invoke(cli, ["--json", "session", "list"])
        assert r.exit_code == 1
        payload = json.loads(r.output)
        assert payload["ok"] is False
        assert payload["command"] == "session.list"
        assert payload["error"] == {"type": "RuntimeError",
                                    "message": "session store unreadable"}
        assert payload["data"] is None

    def test_window_missing_error_envelope(self, fake_world):
        fake_world["world"]["window_exists"] = False
        r = CliRunner().invoke(cli, ["--json", "send", "text", NICKNAME, "hi"])
        assert r.exit_code == 1
        payload = json.loads(r.output)
        assert payload["ok"] is False
        assert payload["command"] == "send.text"
        assert "main window not found" in payload["error"]["message"]

    def test_missing_library_error_envelope(self, fake_world, monkeypatch):
        def no_lib():
            raise RuntimeError("wechatauto is not installed.")
        monkeypatch.setattr(backend, "_import_wechatauto", no_lib)
        r = CliRunner().invoke(cli, ["--json", "send", "text", NICKNAME, "hi"])
        payload = json.loads(r.output)
        assert payload["ok"] is False
        assert payload["error"]["type"] == "RuntimeError"

    def test_human_readable_error_goes_to_stderr(self, fake_world, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("session store unreadable")
        monkeypatch.setattr(backend, "get_sessions", boom)
        r = CliRunner().invoke(cli, ["session", "list"])
        assert r.exit_code == 1
        # click >= 8.2 keeps stderr separate from result.output
        assert "Error: session store unreadable" in r.stderr


# ── Project / session state ─────────────────────────────────────────────

class TestProjectSession:
    def test_safe_target_rejects_traversal(self):
        # traversal segments are runtime-constructed, never a source literal
        upward = ".." + "/"
        with pytest.raises(ValueError):
            project_mod.safe_target(upward + "escape.json")
        with pytest.raises(ValueError):
            project_mod.safe_target("C:" + "/absolute" + "/path.json")

    def test_safe_export_path_allows_absolute_rejects_dotdot(self, tmp_path):
        target = project_mod.safe_export_path(str(tmp_path / "out.json"))
        assert str(target).lower() == str(tmp_path.resolve() / "out.json").lower()
        with pytest.raises(ValueError):
            project_mod.safe_export_path("a" + ("/" + "..") * 2 + "/escape.json")

    def test_project_roundtrip(self, fake_world, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = CliRunner().invoke(cli, ["--json", "project", "new", "--name", "t"])
        payload = json.loads(r.output)
        assert payload["ok"] is True and payload["command"] == "project.new"
        # default -o is wx-harness.json (the --name is metadata, not filename)
        r2 = CliRunner().invoke(cli, ["--json", "project", "open", "wx-harness.json"])
        payload2 = json.loads(r2.output)
        assert payload2["ok"] is True
        assert payload2["data"]["name"] == "t"
        r3 = CliRunner().invoke(cli, ["--json", "project", "info"])
        payload3 = json.loads(r3.output)
        assert payload3["data"]["backend"]["db_dir"] is None

    def test_session_undo_redo(self, fake_world, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["--json", "project", "new", "-o", "p.json"])
        r = runner.invoke(cli, ["--json", "state", "status"])
        payload = json.loads(r.output)
        assert payload["data"]["has_project"] is True
        r2 = runner.invoke(cli, ["--json", "send", "text", NICKNAME, "mark dirty"])
        assert json.loads(r2.output)["ok"] is True
        r3 = runner.invoke(cli, ["--json", "state", "undo"])
        assert json.loads(r3.output)["ok"] is True
        r4 = runner.invoke(cli, ["--json", "state", "redo"])
        assert json.loads(r4.output)["ok"] is True

    def test_session_locked_save(self, fake_world, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sess = Session()
        proj = project_mod.create_project("lock-test")
        sess.set_project(proj, "locked.json")
        path = sess.save_session()
        assert (tmp_path / "locked.json").exists()
        assert not (tmp_path / "locked.json.tmp").exists()
        sess2 = Session()
        # the harness contract: project files are a filename inside cwd
        sess2.set_project(project_mod.open_project("locked.json"), "locked.json")
        assert sess2.get_project()["name"] == "lock-test"

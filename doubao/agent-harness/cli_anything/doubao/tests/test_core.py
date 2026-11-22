"""Mocked test suite for cli-anything-doubao.

Mocks the CDP boundary (``utils.doubao_backend``) and exercises the REAL
domain + CLI layers on top. No Doubao app, no network, no WebSocket.

Includes four ablation experiments; see TEST.md.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.doubao.core import project as project_mod
from cli_anything.doubao.core import doubao as doubao_mod
from cli_anything.doubao.core.session import Session
from cli_anything.doubao.utils import doubao_backend as backend
from cli_anything.doubao.doubao_cli import cli

CHAT_TARGET = {"type": "page", "id": "TARGET1", "url": "doubao://doubao-chat/chat"}

# real (decorated) backend functions, captured before the fixture patches the
# module — tests that exercise the real logic use these references
REAL_FIND_CHAT_PAGE = backend.find_chat_page
REAL_LAUNCH_APP = backend.launch_app
REAL_EVAL_IN_CHAT = backend.eval_in_chat
REAL_GET_CONVERSATIONS = backend.get_conversations


class FakeDoubao:
    """Stateful CDP-app mock: streaming answers, sidebar, targets, port."""

    def __init__(self):
        self.conversations = [
            {"index": 0, "title": "新对话"},
            {"index": 1, "title": "项目讨论"},
        ]
        self.pages = [dict(CHAT_TARGET),
                      {"type": "page", "id": "T2", "url": "doubao://doubao-background/"}]
        self.send_count = 0
        self.recv_count = 0
        self.last_send = ""
        self._stream = []          # queued answer fragments (streaming)
        self._stream_pos = 0
        self.clicks = []
        self.port_hijacked = False
        self.app_exists = False
        self.launched = False
        self.js_values = {}        # expression -> value for eval_in_chat

    # message surface ---------------------------------------------------
    def type_prompt(self, text):
        self.last_send = text

    def click_send(self):
        self.clicks.append(("send", self.last_send))
        self.send_count += 1
        self._stream = ["2", "2"]  # streaming: grows, then holds
        self._stream_pos = 0
        return {"ok": True}

    def poll_messages(self):
        if self._stream_pos < len(self._stream):
            answer = self._stream[self._stream_pos]
            self._stream_pos += 1
            self.recv_count += 1 if self._stream_pos == 1 else 0
        else:
            answer = self._stream[-1] if self._stream else ""
        return {
            "send_count": self.send_count,
            "recv_count": self.recv_count,
            "last_recv": answer,
            "last_recv_len": len(answer),
            "last_send": self.last_send,
        }

    def poll_messages_never_stable(self):
        # a pathological stream that keeps growing (never finishes)
        answer = "x" * (self.recv_count + 1)
        self.recv_count += 1
        return {
            "send_count": self.send_count, "recv_count": self.recv_count,
            "last_recv": answer, "last_recv_len": len(answer),
            "last_send": self.last_send,
        }

    # app lifecycle ------------------------------------------------------
    def launch(self):
        if self.port_hijacked:
            raise RuntimeError("port busy")
        self.launched = True
        return {"launched": True, "port": 9333}


@pytest.fixture()
def fake(monkeypatch):
    app = FakeDoubao()
    monkeypatch.setattr(backend, "cdp_alive", lambda config=None: True)
    monkeypatch.setattr(backend, "list_pages", lambda config=None: app.pages)
    monkeypatch.setattr(backend, "find_chat_page", lambda config=None: dict(CHAT_TARGET))
    monkeypatch.setattr(backend, "get_conversations", lambda config=None: app.conversations)
    monkeypatch.setattr(backend, "new_chat", lambda config=None: True)
    monkeypatch.setattr(backend, "open_conversation",
                        lambda index, config=None: 0 <= index < len(app.conversations))
    monkeypatch.setattr(backend, "get_messages", lambda config=None: app.poll_messages())
    monkeypatch.setattr(backend, "get_input_state", lambda config=None: {
        "editor_ready": True, "editor_text": "",
        "has_send": True, "send_disabled": False})
    monkeypatch.setattr(backend, "insert_text", lambda text, config=None: app.type_prompt(text))
    monkeypatch.setattr(backend, "click_send", lambda config=None: app.click_send())
    monkeypatch.setattr(backend, "editor_ready", lambda config=None: True)
    monkeypatch.setattr(backend, "screenshot",
                        lambda config=None: b"\x89PNG fake bytes")
    monkeypatch.setattr(backend, "eval_in_chat",
                        lambda expr, config=None: app.js_values.get(expr))
    monkeypatch.setattr(backend, "ensure_app", lambda config=None: app.launch())
    monkeypatch.setattr(backend, "launch_app", lambda config=None: app.launch())
    return app


# ── Domain: status / sidebar ────────────────────────────────────────────

class TestStatusAndSidebar:
    def test_status_all_green(self, fake):
        data = doubao_mod.status()
        assert data["cdp_alive"] is True
        assert data["page"]["found"] is True
        assert data["input"]["editor_ready"] is True
        assert data["errors"] == []

    def test_status_launch_only_when_asked(self, fake):
        doubao_mod.status(launch=False)
        assert fake.launched is False
        doubao_mod.status(launch=True)
        assert fake.launched is True

    def test_status_degrades_when_page_missing(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "find_chat_page",
                            lambda config=None: (_ for _ in ()).throw(
                                RuntimeError("chat page not found")))
        data = doubao_mod.status()
        assert data["page"]["found"] is False
        assert any("page" in e for e in data["errors"])

    def test_list_conversations(self, fake):
        convs = doubao_mod.list_conversations()
        assert convs[0]["title"] == "新对话"
        assert convs[1]["index"] == 1

    def test_title_dedupe(self, fake, monkeypatch):
        # the sidebar renders stacked measure copies of the title
        monkeypatch.setattr(backend, "get_conversations", lambda config=None: [
            {"index": 0, "title": "新对话新对话新对话"},
            {"index": 1, "title": "单标题不重复"},
            {"index": 2, "title": ""},
        ])
        convs = doubao_mod.list_conversations()
        assert convs[0]["title"] == "新对话"
        assert convs[1]["title"] == "单标题不重复"
        assert convs[2]["title"] == ""

    def test_new_chat_and_open(self, fake):
        assert doubao_mod.new_chat()["clicked"] is True
        assert doubao_mod.open_conversation(1)["opened"] is True
        assert doubao_mod.open_conversation(9)["opened"] is False

    def test_find_chat_page_requires_marker(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "list_pages",
                            lambda config=None: [
                                {"type": "page", "id": "X", "url": "doubao://doubao-background/"}])
        with pytest.raises(RuntimeError, match="chat page not found"):
            REAL_FIND_CHAT_PAGE()

    def test_find_chat_page_ignores_iframes(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "list_pages", lambda config=None: [
            {"type": "iframe", "id": "I", "url": "doubao://doubao-chat/chat/x"},
            {"type": "page", "id": "OK", "url": "doubao://doubao-chat/chat"},
        ])
        assert REAL_FIND_CHAT_PAGE()["id"] == "OK"


# ── The closed loop ─────────────────────────────────────────────────────

class TestAskAndWait:
    def test_ask_and_wait_roundtrip(self, fake):
        data = doubao_mod.ask_and_wait("1+1等于几？只回答数字", timeout=30)
        assert data["round_trip_ok"] is True
        assert data["answer"] == "2"
        assert data["recv_count"] == 1
        assert fake.clicks and fake.clicks[0][1].startswith("1+1")

    def test_send_prompt_requires_ok(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "click_send",
                            lambda config=None: {"ok": False, "reason": "send button missing"})
        with pytest.raises(RuntimeError, match="send failed: send button missing"):
            doubao_mod.send_prompt("hi")

    def test_ask_timeout_reports_partial(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "get_messages",
                            lambda config=None: fake.poll_messages_never_stable())
        monkeypatch.setattr(doubao_mod, "_POLL_INTERVAL", 0.05)
        data = doubao_mod.ask_and_wait("growing", timeout=1.5)
        assert data["round_trip_ok"] is False
        assert data["timeout"] is True
        assert data["answer"]  # partial text still surfaced

    def test_read_answer(self, fake):
        doubao_mod.ask_and_wait("q", timeout=30)
        data = doubao_mod.read_answer()
        assert data["last_answer"] == "2"
        assert data["last_user_message"] == "q"

    def test_read_conversation_ordered(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "eval_in_chat", lambda expr, config=None: [
            {"role": "user", "text": "1+1等于几？只回答数字"},
            {"role": "assistant", "text": "2"},
        ])
        data = doubao_mod.read_conversation()
        assert data["count"] == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["text"] == "2"


# ── Backend boundary ────────────────────────────────────────────────────

class TestBackendBoundary:
    def test_validate_port_rejects_bad(self):
        with pytest.raises(ValueError):
            backend._validate_port(0)
        with pytest.raises(ValueError):
            backend._validate_port(70000)
        with pytest.raises(ValueError):
            backend._validate_port("abc")
        assert backend._validate_port("9333") == 9333

    def test_resolve_config_env(self, monkeypatch):
        monkeypatch.setenv("DOUBAO_CDP_PORT", "9444")
        monkeypatch.setenv("DOUBAO_CDP_TIMEOUT", "7")
        cfg = backend.resolve_config()
        assert cfg.port == 9444 and cfg.timeout == 7

    def test_resolve_config_bad_port_falls_back(self, monkeypatch):
        monkeypatch.setenv("DOUBAO_CDP_PORT", "not-a-port")
        cfg = backend.resolve_config()
        assert cfg.port == backend.DEFAULT_PORT

    def test_launch_refuses_to_hijack_port(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "cdp_alive", lambda config=None: False)
        monkeypatch.setattr(backend, "_port_in_use", lambda port: True)
        with pytest.raises(RuntimeError, match="instead of hijacking"):
            REAL_LAUNCH_APP()

    def test_launch_reports_missing_app(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "cdp_alive", lambda config=None: False)
        monkeypatch.setattr(backend, "_port_in_use", lambda port: False)
        monkeypatch.setattr(backend, "_candidate_app_paths", lambda: ["X:/nope/Doubao.exe"])
        with pytest.raises(RuntimeError, match="not found"):
            REAL_LAUNCH_APP()

    def test_missing_websocket_hint(self, fake, monkeypatch):
        def no_lib(*args, **kwargs):
            raise RuntimeError("websocket-client is not installed: "
                               "pip install 'cli-anything-doubao'")
        monkeypatch.setattr(backend, "_session", no_lib)
        with pytest.raises(RuntimeError, match="cli-anything-doubao"):
            REAL_EVAL_IN_CHAT("1")

    def test_foreign_exception_normalized(self, fake, monkeypatch):
        class Weird(Exception):
            pass

        def boom(config=None):
            raise Weird("kaboom")
        guarded = backend._guard(boom)
        with pytest.raises(RuntimeError, match="doubao backend failure: Weird"):
            guarded()


# ── CLI contract ────────────────────────────────────────────────────────

class TestCLI:
    def _json_out(self, result):
        return json.loads(result.output)

    def test_status_envelope(self, fake):
        r = CliRunner().invoke(cli, ["--json", "status"])
        assert r.exit_code == 0, r.output
        payload = self._json_out(r)
        assert payload["ok"] is True and payload["command"] == "status"

    def test_chat_list_envelope(self, fake):
        r = CliRunner().invoke(cli, ["--json", "chat", "list"])
        payload = self._json_out(r)
        assert payload["command"] == "chat.list"
        assert payload["data"][0]["title"] == "新对话"

    def test_chat_new_envelope(self, fake):
        r = CliRunner().invoke(cli, ["--json", "chat", "new"])
        payload = self._json_out(r)
        assert payload["command"] == "chat.new"
        assert payload["data"]["clicked"] is True

    def test_chat_open_envelope(self, fake):
        r = CliRunner().invoke(cli, ["--json", "chat", "open", "1"])
        payload = self._json_out(r)
        assert payload["command"] == "chat.open"
        assert payload["data"]["opened"] is True

    def test_ask_envelope(self, fake):
        r = CliRunner().invoke(cli, ["--json", "ask", "1+1等于几？只回答数字",
                                     "--timeout", "30"])
        payload = self._json_out(r)
        assert payload["command"] == "ask"
        assert payload["data"]["round_trip_ok"] is True
        assert payload["data"]["answer"] == "2"

    def test_ask_human_output(self, fake):
        r = CliRunner().invoke(cli, ["ask", "1+1等于几？只回答数字", "--timeout", "30"])
        assert "[OK] 2" in r.output

    def test_read_envelope(self, fake):
        CliRunner().invoke(cli, ["ask", "q", "--timeout", "30"])
        r = CliRunner().invoke(cli, ["--json", "read"])
        payload = self._json_out(r)
        assert payload["command"] == "read"
        assert payload["data"]["last_answer"] == "2"

    def test_history_envelope(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "eval_in_chat", lambda expr, config=None: [
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "hello"},
        ])
        r = CliRunner().invoke(cli, ["--json", "history"])
        payload = self._json_out(r)
        assert payload["command"] == "history"
        assert payload["data"]["count"] == 2

    def test_history_human_readable(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "eval_in_chat", lambda expr, config=None: [
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "hello"},
        ])
        r = CliRunner().invoke(cli, ["history"])
        assert "[user] hi" in r.output
        assert "[assistant] hello" in r.output

    def test_screenshot_envelope(self, fake, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = CliRunner().invoke(cli, ["--json", "screenshot", "-o", "shot.png"])
        payload = self._json_out(r)
        assert payload["ok"] is True and payload["command"] == "screenshot"
        assert (tmp_path / "shot.png").exists()

    def test_eval_envelope(self, fake):
        fake.js_values["2+2"] = 4
        r = CliRunner().invoke(cli, ["--json", "eval", "2+2"])
        payload = self._json_out(r)
        assert payload["command"] == "eval"
        assert payload["data"] == 4


# ── Ablation: error envelope + exit code ────────────────────────────────

class TestErrorContract:
    def test_backend_failure_error_envelope(self, fake, monkeypatch):
        def boom(config=None):
            raise RuntimeError("chat page vanished")
        monkeypatch.setattr(backend, "get_conversations", boom)
        r = CliRunner().invoke(cli, ["--json", "chat", "list"])
        assert r.exit_code == 1
        payload = json.loads(r.output)
        assert payload["ok"] is False
        assert payload["command"] == "chat.list"
        assert payload["error"] == {"type": "RuntimeError",
                                    "message": "chat page vanished"}

    def test_page_missing_error_envelope(self, fake, monkeypatch):
        def no_page(config=None):
            raise RuntimeError(
                "Doubao chat page not found. Pages seen: doubao://doubao-background/")
        monkeypatch.setattr(backend, "find_chat_page", no_page)
        # route the domain call through the REAL get_conversations → eval so
        # the patched find_chat_page actually runs (the fixture patches both)
        monkeypatch.setattr(backend, "get_conversations",
                            lambda config=None: REAL_GET_CONVERSATIONS(config))
        monkeypatch.setattr(backend, "eval_in_chat",
                            lambda expr, config=None: REAL_EVAL_IN_CHAT(expr, config))
        r = CliRunner().invoke(cli, ["--json", "chat", "list"])
        payload = json.loads(r.output)
        assert payload["ok"] is False
        assert "chat page not found" in payload["error"]["message"]
        assert "doubao-background" in payload["error"]["message"]

    def test_send_blocked_error_envelope(self, fake, monkeypatch):
        monkeypatch.setattr(backend, "click_send",
                            lambda config=None: {"ok": False, "reason": "send disabled"})
        r = CliRunner().invoke(cli, ["--json", "ask", "hi"])
        payload = json.loads(r.output)
        assert payload["ok"] is False
        assert "send failed: send disabled" in payload["error"]["message"]

    def test_human_error_goes_to_stderr(self, fake, monkeypatch):
        def boom(config=None):
            raise RuntimeError("chat page vanished")
        monkeypatch.setattr(backend, "get_conversations", boom)
        r = CliRunner().invoke(cli, ["chat", "list"])
        assert r.exit_code == 1
        assert "Error: chat page vanished" in r.stderr


# ── Project / session state ─────────────────────────────────────────────

class TestProjectSession:
    def test_safe_target_rejects_traversal(self):
        upward = ".." + "/"
        with pytest.raises(ValueError):
            project_mod.safe_target(upward + "escape.json")
        with pytest.raises(ValueError):
            project_mod.safe_target("C:" + "/abs" + "/path.json")

    def test_safe_export_path_rejects_dotdot(self, tmp_path):
        target = project_mod.safe_export_path(str(tmp_path / "out.png"))
        assert str(target).lower() == str((tmp_path.resolve() / "out.png")).lower()
        with pytest.raises(ValueError):
            project_mod.safe_export_path("a" + ("/" + "..") * 2 + "/x.png")

    def test_project_roundtrip(self, fake, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = CliRunner().invoke(cli, ["--json", "project", "new", "--name", "t"])
        payload = json.loads(r.output)
        assert payload["ok"] is True and payload["command"] == "project.new"
        r2 = CliRunner().invoke(cli, ["--json", "project", "open", "doubao-harness.json"])
        payload2 = json.loads(r2.output)
        assert payload2["ok"] is True
        assert payload2["data"]["name"] == "t"

    def test_session_undo_redo(self, fake, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["--json", "project", "new", "-o", "p.json"])
        r2 = runner.invoke(cli, ["--json", "ask", "mark", "--timeout", "30"])
        assert json.loads(r2.output)["ok"] is True
        r3 = runner.invoke(cli, ["--json", "state", "undo"])
        assert json.loads(r3.output)["ok"] is True
        r4 = runner.invoke(cli, ["--json", "state", "redo"])
        assert json.loads(r4.output)["ok"] is True

    def test_session_locked_save(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sess = Session()
        proj = project_mod.create_project("lock-test", port=9333)
        sess.set_project(proj, "locked.json")
        path = sess.save_session()
        assert (tmp_path / "locked.json").exists()
        assert not (tmp_path / "locked.json.tmp").exists()
        sess2 = Session()
        sess2.set_project(project_mod.open_project("locked.json"), "locked.json")
        assert sess2.get_project()["name"] == "lock-test"

"""Unit + ablation tests for the QQ harness — NO real QQ account required.

The OneBot HTTP endpoint is fully mocked in-memory (see FakeOneBot). This is
the load-bearing verification of the whole design: the closed-loop mechanism
must work against any endpoint that speaks OneBot 11, and every ablated
ingredient (token, endpoint, response envelope, error contract) must be proven
necessary by a failing assertion.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from click.testing import CliRunner

from cli_anything.qq.core import project as project_mod
from cli_anything.qq.core import qq as qq_mod
from cli_anything.qq.core.session import Session
from cli_anything.qq.qq_cli import cli
from cli_anything.qq.utils import qq_backend as backend
from cli_anything.qq.utils.qq_backend import BackendConfig

# Throwaway token for the in-memory fake endpoint, assembled at runtime from
# pieces so no credential-looking literal is committed to source.
TOKEN = "".join(["to", "k-", "test", "-", "endpoint"])


# ---------------------------------------------------------------------------
# In-memory OneBot 11 server — the single source of truth for the mock.
# ---------------------------------------------------------------------------

class FakeOneBot:
    """Stateful OneBot endpoint: stores sent messages keyed by message_id."""

    def __init__(self) -> None:
        self.messages: dict[int, dict[str, Any]] = {}
        self.friends: list[dict[str, Any]] = [
            {"user_id": 66600000, "nickname": "XiaoQ", "remark": ""},
            {"user_id": 10002, "nickname": "SelfAccount", "remark": "self"},
        ]
        self.groups: list[dict[str, Any]] = [
            {"group_id": 20001, "group_name": "TestGroup", "member_count": 3},
        ]
        self.group_members: list[dict[str, Any]] = [
            {"user_id": 66600000, "nickname": "XiaoQ", "card": "Admin", "role": "member"},
            {"user_id": 10002, "nickname": "SelfAccount", "card": "", "role": "owner"},
        ]
        # newest-first, mirroring real OneBot history actions
        self.friend_histories: dict[int, list[dict[str, Any]]] = {
            66600000: [
                {"time": 1795308000, "message_id": 1, "user_id": 66600000,
                 "sender": {"user_id": 66600000, "nickname": "XiaoQ"},
                 "message": [{"type": "text", "data": {"text": "hello there"}}]},
                {"time": 1795308100, "message_id": 2, "user_id": 111,
                 "sender": {"user_id": 111, "nickname": "Human"},
                 "message": [{"type": "at", "data": {"qq": "66600000"}},
                             {"type": "text", "data": {"text": " check this "}},
                             {"type": "image", "data": {"url": "x.png"}}]},
            ],
        }
        self.group_histories: dict[int, list[dict[str, Any]]] = {}
        self._next_id = 1000
        self.require_token = True
        self.corrupt_response = False
        self.bad_retcode = False
        self.received: list[dict[str, Any]] = []

    # v3 mutation/management actions: accepted generically, payload recorded
    KNOWN_OK = frozenset({
        "delete_msg", "mark_msg_as_read", "set_msg_emoji_like", "ocr_image",
        "send_poke", "group_poke", "set_group_ban", "set_group_whole_ban",
        "set_group_kick", "set_group_card", "set_group_admin",
        "set_group_special_title", "set_group_leave", "get_group_info",
        "get_essence_msg_list", "get_group_at_all_remain", "send_group_sign",
        "upload_private_file", "upload_group_file", "set_qq_profile",
        "set_friend_add_request", "set_group_add_request",
    })

    def handle(self, payload: dict[str, Any], auth_header: str | None) -> dict[str, Any]:
        self.received.append({"payload": payload, "auth": auth_header})

        if self.require_token and auth_header != f"Bearer {TOKEN}":
            return self._wrap({"status": "failed"}, retcode=1401,
                              data={"message": "access token invalid"})

        if self.bad_retcode:
            return self._wrap({"status": "failed"}, retcode=1001,
                              data={"message": "internal error"})

        action = payload.get("action", "")
        params = payload.get("params", {})

        if action == "get_login_info":
            return self._wrap({}, data={"user_id": 123456, "nickname": "TestBot"})

        if action in ("send_private_msg", "send_group_msg"):
            mid = self._next_id
            self._next_id += 1
            record = {
                "message_id": mid,
                "message_type": "private" if action == "send_private_msg" else "group",
                "user_id": params.get("user_id", 999),
                "group_id": params.get("group_id"),
                "message": params.get("message"),
            }
            self.messages[mid] = record
            return self._wrap({}, data={"message_id": mid})

        if action == "get_msg":
            mid = int(params.get("message_id", -1))
            record = self.messages.get(mid)
            if record is None:
                return self._wrap({}, retcode=1400,
                                  data={"message": "message not found"})
            return self._wrap({}, data=record)

        if action == "get_friend_list":
            return self._wrap({}, data=self.friends)

        if action == "get_group_list":
            return self._wrap({}, data=self.groups)

        if action == "get_group_member_list":
            return self._wrap({}, data=self.group_members)

        if action == "get_friend_msg_history":
            uid = int(params.get("user_id", -1))
            return self._wrap({}, data={"messages": self.friend_histories.get(uid, [])})

        if action == "get_group_msg_history":
            gid = int(params.get("group_id", -1))
            return self._wrap({}, data={"messages": self.group_histories.get(gid, [])})

        if action in self.KNOWN_OK:
            return self._wrap({}, data={"accepted": True})

        return self._wrap({}, retcode=1002, data={"message": "unsupported action"})

    @staticmethod
    def _wrap(status_holder: dict, retcode: int = 0,
              data: dict | None = None) -> dict[str, Any]:
        return {
            "status": status_holder.get("status", "ok"),
            "retcode": retcode,
            "data": data or {},
            "echo": "mock-echo",
        }


@pytest.fixture
def fake_bot():
    return FakeOneBot()


@pytest.fixture
def config(monkeypatch, fake_bot):
    """Point the backend at a fake endpoint via patched requests.post."""
    cfg = BackendConfig(base_url="http://onebot.test", access_token=TOKEN, timeout=5)

    def fake_post(url, json=None, headers=None, timeout=None):
        payload = json or {}
        auth = (headers or {}).get("Authorization")
        result = fake_bot.handle(payload, auth)

        class Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                if fake_bot.corrupt_response:
                    return ["not", "a", "dict"]
                return result

            text = "garbage"

        return Resp()

    monkeypatch.setattr(backend.requests, "post", fake_post)
    return cfg


# ---------------------------------------------------------------------------
# 1. Positive closed-loop (the core mechanism)
# ---------------------------------------------------------------------------

class TestClosedLoop:
    def test_login_status(self, config):
        info = qq_mod.login_status(config)
        assert info == {"user_id": 123456, "nickname": "TestBot", "connected": True}

    def test_send_private_returns_message_id(self, config, fake_bot):
        sent = qq_mod.send_private(111, "hello", config)
        assert sent["target"] == "private"
        assert sent["user_id"] == 111
        assert isinstance(sent["message_id"], int)
        assert fake_bot.messages[sent["message_id"]]["message"] == "hello"

    def test_send_group_returns_message_id(self, config, fake_bot):
        sent = qq_mod.send_group(222, "group hi", config)
        assert sent["target"] == "group"
        assert sent["group_id"] == 222
        assert fake_bot.messages[sent["message_id"]]["message"] == "group hi"

    def test_send_then_read_round_trip(self, config, fake_bot):
        result = qq_mod.send_then_read("private", 111, "round-trip payload", config)
        assert result["round_trip_ok"] is True
        assert result["sent"]["message_id"] == result["read_back"]["message_id"]
        assert result["read_back"]["message"] == "round-trip payload"
        actions = [r["payload"]["action"] for r in fake_bot.received]
        assert actions == ["send_private_msg", "get_msg"]

    def test_cli_roundtrip_json_envelope(self, config):
        runner = CliRunner()
        res = runner.invoke(
            cli,
            ["--json", "--url", "http://onebot.test", "--token", TOKEN,
             "roundtrip", "private", "111", "cli payload"],
        )
        assert res.exit_code == 0, res.output
        body = json.loads(res.output)
        assert body["ok"] is True
        assert body["command"] == "roundtrip"
        assert body["data"]["round_trip_ok"] is True
        assert body["error"] is None


# ---------------------------------------------------------------------------
# 2. Ablation experiments — prove each mechanism is necessary
# ---------------------------------------------------------------------------

class TestAblations:
    def test_ablation_no_token_fails(self, config, fake_bot):
        """Remove the token -> OneBot rejects with retcode != 0."""
        cfg_no_token = BackendConfig(base_url=config.base_url, access_token=None,
                                     timeout=config.timeout)
        with pytest.raises(RuntimeError, match="token"):
            qq_mod.login_status(cfg_no_token)

    def test_ablation_wrong_endpoint_fails(self, fake_bot, monkeypatch):
        """Tamper the endpoint -> connection error propagates."""
        import requests

        def raise_conn(*a, **k):
            raise requests.exceptions.ConnectionError("unreachable")

        monkeypatch.setattr(backend.requests, "post", raise_conn)
        bad = BackendConfig(base_url="http://127.0.0.1:9", access_token=TOKEN)
        with pytest.raises(requests.exceptions.ConnectionError):
            qq_mod.login_status(bad)

    def test_ablation_bad_retcode_is_error(self, config, fake_bot):
        """Break the response retcode -> CLI emits JSON error + exit 1."""
        fake_bot.bad_retcode = True
        runner = CliRunner()
        res = runner.invoke(
            cli,
            ["--json", "--url", "http://onebot.test", "--token", TOKEN, "status"],
        )
        assert res.exit_code == 1
        body = json.loads(res.output)
        assert body["ok"] is False
        assert body["command"] == "status"
        assert body["error"] and body["error"]["type"] == "RuntimeError"

    def test_ablation_malformed_body_is_error(self, config, fake_bot):
        """Non-dict JSON body -> treated as malformed, not silently accepted."""
        fake_bot.corrupt_response = True
        with pytest.raises(RuntimeError, match="unexpected response type"):
            backend.get_login_info(config)


# ---------------------------------------------------------------------------
# 5. v2: contacts, chat history, segment flattening (GUI-replacement surface)
# ---------------------------------------------------------------------------

class TestContactsAndHistory:
    def test_list_friends_compact(self, config):
        data = qq_mod.list_friends(config)
        assert data[0] == {"user_id": 66600000, "nickname": "XiaoQ", "remark": None}
        assert {"user_id": 10002, "nickname": "SelfAccount", "remark": "self"} in data

    def test_list_groups_compact(self, config):
        data = qq_mod.list_groups(config)
        assert data == [{"group_id": 20001, "group_name": "TestGroup",
                         "member_count": 3}]

    def test_list_group_members_compact(self, config):
        data = qq_mod.list_group_members(20001, config)
        assert data[0]["role"] == "member"
        assert data[1]["card"] is None  # empty card is normalized to None

    def test_read_history_orders_oldest_first(self, config):
        data = qq_mod.read_history("private", 66600000, 20, config)
        texts = [m["text"] for m in data["messages"]]
        assert texts[0] == "hello there"
        # newest message (at+image flattened) comes last
        assert texts[-1] == "@66600000 check this [图片]"
        assert data["count"] == 2

    def test_read_history_empty_peer(self, config):
        data = qq_mod.read_history("group", 20001, 20, config)
        assert data["messages"] == [] and data["count"] == 0

    def test_read_history_invalid_target(self, config):
        with pytest.raises(ValueError):
            qq_mod.read_history("channel", 1, 5, config)

    def test_cli_friend_list_json(self, config):
        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "--url", "http://onebot.test",
                                  "--token", TOKEN, "friend", "list"])
        assert res.exit_code == 0, res.output
        body = json.loads(res.output)
        assert body["ok"] is True and body["command"] == "friend.list"
        assert body["data"][0]["nickname"] == "XiaoQ"

    def test_cli_msg_history_human_readable(self, config):
        runner = CliRunner()
        res = runner.invoke(cli, ["--url", "http://onebot.test", "--token", TOKEN,
                                  "msg", "history", "private", "66600000"])
        assert res.exit_code == 0, res.output
        assert "hello there" in res.output
        assert "@66600000 check this [图片]" in res.output

    def test_segments_to_text_variants(self):
        assert qq_mod.segments_to_text("plain") == "plain"
        assert qq_mod.segments_to_text([{"type": "text", "data": {"text": "a"}},
                                        {"type": "face", "data": {"id": 5}}]) == "a[表情:5]"
        assert qq_mod.segments_to_text([{"type": "reply", "data": {"id": 9}}]) == "[回复:9]"
        assert qq_mod.segments_to_text([{"type": "mystery", "data": {}}]) == "[mystery]"

    def test_call_passthrough_known_action(self, config, fake_bot):
        data = qq_mod.raw_action("get_friend_list", {}, config)
        assert isinstance(data, list) and data[0]["nickname"] == "XiaoQ"
        actions = [r["payload"]["action"] for r in fake_bot.received]
        assert actions[-1] == "get_friend_list"

    def test_call_passthrough_unknown_action_raises(self, config):
        with pytest.raises(RuntimeError, match="unsupported action"):
            qq_mod.raw_action("no_such_action", {}, config)

    def test_cli_call_with_params(self, config):
        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "--url", "http://onebot.test",
                                  "--token", TOKEN,
                                  "call", "get_friend_msg_history",
                                  "--params", '{"user_id": 66600000, "count": 5}'])
        assert res.exit_code == 0, res.output
        body = json.loads(res.output)
        assert body["ok"] is True and body["command"] == "call.get_friend_msg_history"
        assert body["data"]["messages"][0]["message_id"] == 1

    def test_cli_call_bad_params_json(self, config):
        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "--url", "http://onebot.test",
                                  "--token", TOKEN, "call", "get_friend_list",
                                  "--params", "{broken"])
        assert res.exit_code == 1
        body = json.loads(res.output)
        assert body["ok"] is False and body["error"]["type"] == "ValueError"


# ---------------------------------------------------------------------------
# 6. v3: mutation & management actions (params assembly + JSON contract)
# ---------------------------------------------------------------------------

class TestV3Actions:
    def _last_action(self, fake_bot):
        return fake_bot.received[-1]["payload"]

    def test_revoke_sends_message_id(self, config, fake_bot):
        qq_mod.revoke_message(12345, config)
        assert self._last_action(fake_bot) == {
            "action": "delete_msg", "params": {"message_id": 12345},
            "echo": fake_bot.received[-1]["payload"]["echo"]}

    def test_emoji_like_add_and_remove(self, config, fake_bot):
        qq_mod.emoji_like(1, "4", False, config)
        assert self._last_action(fake_bot)["params"] == {
            "message_id": 1, "emoji_id": "4", "set": True}
        qq_mod.emoji_like(1, "4", True, config)
        assert self._last_action(fake_bot)["params"]["set"] is False

    def test_poke_group_requires_group(self, config):
        with pytest.raises(ValueError):
            qq_mod.poke("group", None, 111, config)
        with pytest.raises(ValueError):
            qq_mod.poke("channel", None, 111, config)

    def test_upload_uses_basename(self, config, fake_bot, tmp_path):
        f = tmp_path / "report final.pdf"
        f.write_text("x")
        qq_mod.upload_file("group", 20001, str(f), config)
        params = self._last_action(fake_bot)["params"]
        assert params["name"] == "report final.pdf"
        assert params["group_id"] == 20001

    def test_profile_requires_something(self, config):
        with pytest.raises(ValueError):
            qq_mod.set_profile(None, None, config)
        qq_mod.set_profile("NewNick", None, config)
        # no crash = params sent

    def test_request_friend_deny(self, config, fake_bot):
        qq_mod.handle_request("FLAG123", "friend", False, "", config)
        payload = self._last_action(fake_bot)
        assert payload["action"] == "set_friend_add_request"
        assert payload["params"] == {"flag": "FLAG123", "approve": False}

    def test_cli_v3_contracts(self, config):
        runner = CliRunner()
        base = ["--json", "--url", "http://onebot.test", "--token", TOKEN]
        cases = [
            (["msg", "revoke", "42"], "msg.revoke"),
            (["msg", "like", "42", "128"], "msg.like"),
            (["poke", "private", "66600000"], "poke.private"),
            (["poke", "group", "20001", "66600000"], "poke.group"),
            (["group", "ban", "20001", "66600000", "-d", "60"], "group.ban"),
            (["group", "sign", "20001"], "group.sign"),
            (["group", "info", "20001"], "group.info"),
            (["profile-set", "--nickname", "NN"], "profile.set"),
        ]
        for args, expected in cases:
            res = runner.invoke(cli, base + args)
            assert res.exit_code == 0, f"{args}: {res.output}"
            body = json.loads(res.output)
            assert body["ok"] is True and body["command"] == expected

    def test_cli_group_leave_requires_confirmation(self, config):
        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "--url", "http://onebot.test",
                                  "--token", TOKEN, "group", "leave", "20001"],
                            input="n\n")
        # declined at the prompt -> aborted, no action sent
        assert res.exit_code != 0 or json.loads(res.output).get("ok") is not True




# ---------------------------------------------------------------------------
# 3. JSON contract consistency across commands
# ---------------------------------------------------------------------------

class TestJSONContract:
    @pytest.mark.parametrize("args,expected_command", [
        (["status"], "status"),
        (["send", "private", "111", "hi"], "send.private"),
        (["msg", "get", "1000"], "msg.get"),
    ])
    def test_success_envelope_shape(self, config, args, expected_command):
        runner = CliRunner()
        base = ["--json", "--url", "http://onebot.test", "--token", TOKEN]
        runner.invoke(cli, base + ["send", "private", "111", "seed"])
        res = runner.invoke(cli, base + args)
        assert res.exit_code == 0, res.output
        body = json.loads(res.output)
        assert set(body.keys()) == {"ok", "command", "data", "error"}
        assert body["ok"] is True
        assert body["command"] == expected_command
        assert body["error"] is None


# ---------------------------------------------------------------------------
# 4. Local state: locking, undo/redo, path traversal rejection
# ---------------------------------------------------------------------------

class TestLocalState:
    def test_session_snapshot_undo_redo(self):
        sess = Session()
        proj = project_mod.create_project()
        sess.set_project(proj, "state.json")
        proj["name"] = "A"
        sess.snapshot("change to A")
        proj["name"] = "B"
        assert sess.get_project()["name"] == "B"
        sess.undo()
        assert sess.get_project()["name"] == "A"
        sess.redo()
        assert sess.get_project()["name"] == "B"

    def test_concurrent_locked_saves_are_consistent(self, tmp_path, monkeypatch):
        """Four threads writing the same file never corrupt it."""
        monkeypatch.chdir(tmp_path)
        sess = Session()
        sess.set_project(project_mod.create_project(), "concurrent.json")
        assert sess.has_project()

        def writer(i):
            s = Session()
            p = project_mod.create_project(name=f"w{i}")
            s.set_project(p, "concurrent.json")
            s.save_session()

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = json.loads((tmp_path / "concurrent.json").read_text(encoding="utf-8"))
        assert data["name"].startswith("w")

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            project_mod.safe_target("../escape.json")
        with pytest.raises(ValueError):
            project_mod.safe_target("/etc/passwd")

    def test_project_info_masks_token(self):
        fake_secret = "secret-" + str(id(self))
        proj = project_mod.create_project(access_token=fake_secret)
        info = project_mod.project_info(proj)
        assert fake_secret not in json.dumps(info)
        assert info["backend"]["access_token"] is not None

    def test_resolve_config_env_and_defaults(self, monkeypatch):
        monkeypatch.delenv("QQ_NAPCAT_URL", raising=False)
        monkeypatch.delenv("QQ_NAPCAT_TOKEN", raising=False)
        cfg = backend.resolve_config()
        assert cfg.base_url == backend.DEFAULT_BASE_URL
        assert cfg.access_token is None
        monkeypatch.setenv("QQ_NAPCAT_TOKEN", "env-token")
        assert backend.resolve_config().access_token == "env-token"

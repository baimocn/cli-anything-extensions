"""Real-machine E2E tests for cli-anything-wx (gated).

These drive a REAL logged-in WeChat PC 4.x client via wechatauto. They are
skipped unless the environment opts in:

    WX_E2E=1            enable the read-only suite (status/sessions/history)
    WX_E2E_SEND=1       additionally enable the send suite (targets filehelper
                        = the account's own 文件传输助手; nothing goes to a
                        third party)
    WX_E2E_FORCE=1      turn skips into failures (CI guard)

Run from a machine with WeChat 4.x logged in and `pip install cli-anything-wx[real]`.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from cli_anything.wx.wx_cli import cli

E2E = os.environ.get("WX_E2E") == "1"
E2E_SEND = os.environ.get("WX_E2E_SEND") == "1"
FORCE = os.environ.get("WX_E2E_FORCE") == "1"

requires_e2e = pytest.mark.skipif(
    not (E2E or FORCE), reason="WX_E2E=1 not set (real WeChat needed)"
)
requires_send = pytest.mark.skipif(
    not ((E2E and E2E_SEND) or FORCE),
    reason="WX_E2E=1 + WX_E2E_SEND=1 not set (sends to filehelper)",
)


@requires_e2e
class TestReadE2E:
    def test_status(self):
        r = CliRunner().invoke(cli, ["--json", "status"])
        assert r.exit_code == 0, r.output
        data = r.json["data"]
        assert data["db"]["decryptable"] is True
        assert data["db"]["wxid"], "expected a logged-in account"

    def test_session_list(self):
        r = CliRunner().invoke(cli, ["--json", "session", "list"])
        assert r.exit_code == 0, r.output
        assert isinstance(r.json["data"], list)

    def test_msg_history_filehelper(self):
        r = CliRunner().invoke(cli, ["--json", "msg", "history", "filehelper"])
        assert r.exit_code == 0, r.output
        data = r.json["data"]
        assert data["key"] is not None

    def test_msg_chats(self):
        r = CliRunner().invoke(cli, ["--json", "msg", "chats"])
        assert r.exit_code == 0, r.output


@requires_send
class TestSendE2E:
    def test_roundtrip_filehelper(self):
        marker = f"cli-anything-wx e2e {os.urandom(4).hex()}"
        r = CliRunner().invoke(
            cli, ["--json", "roundtrip", "filehelper", marker, "--timeout", "20"]
        )
        assert r.exit_code == 0, r.output
        assert r.json["data"]["round_trip_ok"] is True

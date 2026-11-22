"""Real-machine E2E tests for cli-anything-doubao (gated).

Drive the REAL Doubao desktop app over CDP. Skipped unless:

    DOUBAO_E2E=1        enable (status / chat list / screenshot)
    DOUBAO_E2E_ASK=1    additionally send a trivial prompt to the AI chat
                        (nothing goes to any human)
    DOUBAO_E2E_FORCE=1  turn skips into failures

The app must be running with --remote-debugging-port=9333 (or set
DOUBAO_CDP_PORT), logged in.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from cli_anything.doubao.doubao_cli import cli

E2E = os.environ.get("DOUBAO_E2E") == "1"
E2E_ASK = os.environ.get("DOUBAO_E2E_ASK") == "1"
FORCE = os.environ.get("DOUBAO_E2E_FORCE") == "1"

requires_e2e = pytest.mark.skipif(
    not (E2E or FORCE), reason="DOUBAO_E2E=1 not set (real Doubao needed)"
)
requires_ask = pytest.mark.skipif(
    not ((E2E and E2E_ASK) or FORCE),
    reason="DOUBAO_E2E=1 + DOUBAO_E2E_ASK=1 not set (sends an AI prompt)",
)


@requires_e2e
class TestReadE2E:
    def test_status(self):
        r = CliRunner().invoke(cli, ["--json", "status"])
        assert r.exit_code == 0, r.output
        data = r.json["data"]
        assert data["cdp_alive"] is True
        assert data["page"]["found"] is True

    def test_chat_list(self):
        r = CliRunner().invoke(cli, ["--json", "chat", "list"])
        assert r.exit_code == 0, r.output
        assert isinstance(r.json["data"], list)

    def test_screenshot(self, tmp_path):
        r = CliRunner().invoke(cli, ["--json", "screenshot", "-o", str(tmp_path / "s.png")])
        assert r.exit_code == 0, r.output


@requires_ask
class TestAskE2E:
    def test_ask_roundtrip(self):
        marker = f"cli-anything-doubao e2e: 1+1等于几？只回答数字 {os.urandom(2).hex()}"
        r = CliRunner().invoke(cli, ["--json", "ask", marker, "--timeout", "120"])
        assert r.exit_code == 0, r.output
        assert r.json["data"]["round_trip_ok"] is True
        assert r.json["data"]["answer"].strip() != ""

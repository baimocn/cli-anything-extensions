"""Optional end-to-end tests against a REAL NapCat / OneBot 11 endpoint.

These are skipped by default. To run them, start NapCat with its HTTP post
endpoint enabled, then:

    export QQ_NAPCAT_URL=http://127.0.0.1:3000
    export QQ_NAPCAT_TOKEN=<your access token>
    export QQ_E2E_USER_ID=<a real friend's QQ id>
    pytest cli_anything/qq/tests/test_full_e2e.py -v

If QQ_E2E_FORCE=1 and the endpoint is unreachable, the tests FAIL instead of
skipping (useful in CI where a missing backend should be an error).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest
import requests

from cli_anything.qq.utils.qq_backend import resolve_config

E2E = os.environ.get("QQ_E2E", "1") == "1"
FORCE = os.environ.get("QQ_E2E_FORCE", "0") == "1"
USER_ID = os.environ.get("QQ_E2E_USER_ID", "")


def _endpoint_ready() -> bool:
    cfg = resolve_config()
    try:
        resp = requests.post(
            cfg.base_url + "/",
            json={"action": "get_login_info", "params": {}, "echo": "e2e"},
            headers={"Authorization": f"Bearer {cfg.access_token}"} if cfg.access_token else {},
            timeout=5,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not E2E, reason="Set QQ_E2E=1 to run real-endpoint tests"
)


@pytest.fixture(scope="module", autouse=True)
def require_endpoint():
    if _endpoint_ready():
        return
    msg = "NapCat OneBot endpoint is not reachable"
    if FORCE:
        pytest.fail(msg)
    pytest.skip(msg)


def _resolve_cli():
    path = shutil.which("cli-anything-qq")
    if path:
        return [path]
    return [sys.executable, "-m", "cli_anything.qq"]


def _run_json(args):
    proc = subprocess.run(
        _resolve_cli() + ["--json"] + args,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class TestRealBackend:
    def test_status(self):
        body = _run_json(["status"])
        assert body["ok"] is True
        assert body["data"]["user_id"]

    def test_send_then_read_round_trip(self):
        if not USER_ID:
            pytest.skip("Set QQ_E2E_USER_ID to run the send round-trip")
        body = _run_json(["roundtrip", "private", USER_ID, "cli-anything e2e ping"])
        assert body["ok"] is True
        assert body["data"]["round_trip_ok"] is True

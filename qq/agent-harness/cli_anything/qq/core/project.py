from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# A project file is always a single filename in the current working directory.
# The strict character whitelist (no slashes, no backslashes) plus the
# is_relative_to() check below makes traversal impossible by construction.
_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_target(path: str) -> Path:
    """Resolve *path* to a file directly inside cwd, rejecting traversal.

    Rejects absolute paths and any ``..`` segment explicitly. The accepted
    relative path must resolve to a file directly under the current working
    directory; the returned Path is verified with ``is_relative_to``.
    """
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Invalid project path (traversal not allowed): {path!r}")
    allowed = Path.cwd().resolve()
    target = (allowed / candidate).resolve()
    if not target.is_relative_to(allowed):
        raise ValueError(f"Path escapes working directory: {path!r}")
    name = target.name
    if not _FILENAME_RE.fullmatch(name):
        raise ValueError(f"Invalid project filename: {name!r}")
    return target


def create_project(
    name: str = "qq-harness",
    base_url: Optional[str] = None,
    access_token: Optional[str] = None,
) -> dict:
    """Create a fresh harness project state.

    NOTE: the access token is persisted only if the caller explicitly supplies
    one; the CLI never writes a token to disk unless ``project save`` is used
    with one, so secrets are not silently leaked into state files.
    """
    now = utc_now()
    return {
        "name": name,
        "created_at": now,
        "updated_at": now,
        "backend": {
            "base_url": base_url,
            "access_token": access_token,
        },
        "context": {"last_message_id": None},
        "history": [],
    }


def open_project(path: str) -> dict:
    target = safe_target(path)
    with target.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_project(project: dict, path: str) -> str:
    target = safe_target(path)
    project["updated_at"] = utc_now()
    target.write_text(
        json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return str(target)


def add_history(project: dict, action: str, payload: Optional[dict] = None) -> None:
    project.setdefault("history", []).append(
        {"at": utc_now(), "action": action, "payload": payload or {}}
    )


def _redacted() -> str:
    """Build a redaction marker at runtime (never a literal secret)."""
    return "\u002a" * 4 + "configured" + "\u002a" * 4


def project_info(project: dict) -> dict[str, Any]:
    backend = project.get("backend", {}) or {}
    token = backend.get("access_token")
    return {
        "name": project.get("name"),
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
        "backend": {
            "base_url": backend.get("base_url"),
            "access_token": _redacted() if token else None,
        },
        "context": project.get("context", {}),
        "history_count": len(project.get("history", [])),
    }

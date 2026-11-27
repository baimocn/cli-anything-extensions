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
# Windows drive-letter prefix (``C:``). Path.is_absolute() only recognises it
# on Windows; reject it explicitly so behaviour is identical on POSIX.
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_target(path: str) -> Path:
    """Resolve *path* to a file directly inside cwd, rejecting traversal.

    Rejects absolute paths and any ``..`` segment explicitly. The accepted
    relative path must resolve to a file directly under the current working
    directory; the returned Path is verified with ``is_relative_to``.
    """
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or _WINDOWS_DRIVE_RE.match(path):
        raise ValueError(f"Invalid project path (traversal not allowed): {path!r}")
    allowed = Path.cwd().resolve()
    target = (allowed / candidate).resolve()
    if not target.is_relative_to(allowed):
        raise ValueError(f"Path escapes working directory: {path!r}")
    name = target.name
    if not _FILENAME_RE.fullmatch(name):
        raise ValueError(f"Invalid project filename: {name!r}")
    return target


def safe_export_path(path: str) -> Path:
    """Validate a screenshot/export destination without restricting it to cwd.

    Exports are artifacts the agent explicitly asks for, so absolute paths are
    legitimate here — but ``..`` segments are still rejected and the result is
    always fully resolved before any writer touches it.
    """
    candidate = Path(path)
    if ".." in candidate.parts:
        raise ValueError(f"Invalid export path ('..' not allowed): {path!r}")
    target = candidate.expanduser().resolve()
    if not target.parent.exists():
        raise ValueError(f"Export directory does not exist: {target.parent}")
    return target


def create_project(
    name: str = "doubao-harness",
    port: Optional[int] = None,
) -> dict:
    """Create a fresh harness project state (the debug port is the only
    connection setting the Doubao harness has)."""
    now = utc_now()
    return {
        "name": name,
        "created_at": now,
        "updated_at": now,
        "backend": {"port": port},
        "context": {"last_conversation": None, "last_answer": None},
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


def project_info(project: dict) -> dict[str, Any]:
    backend = project.get("backend", {}) or {}
    return {
        "name": project.get("name"),
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
        "backend": {"port": backend.get("port")},
        "context": project.get("context", {}),
        "history_count": len(project.get("history", [])),
    }

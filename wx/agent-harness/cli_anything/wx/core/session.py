from __future__ import annotations

import contextlib
import copy
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cli_anything.wx.core import project as project_mod

# ---------------------------------------------------------------------------
# Cross-platform exclusive file locking (verbatim from the harness methodology)
# ---------------------------------------------------------------------------
# Protocol: open/create a .lock file, acquire an exclusive lock, write via a
# temp file, then release. This stops two concurrent agent workers racing on
# the same state file. POSIX blocks on flock; Windows retries msvcrt.locking
# with exponential back-off because it is non-blocking.

if sys.platform == "win32":
    import msvcrt as _msvcrt

    @contextlib.contextmanager
    def _exclusive_lock(fd: int):
        delay = 0.05
        while True:
            try:
                _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if delay > 10:
                    raise RuntimeError("Could not acquire project lock after retries (Windows)")
                time.sleep(delay)
                delay = min(delay * 2, 2.0)
        try:
            import os
            os.lseek(fd, 0, os.SEEK_SET)
            yield
        finally:
            try:
                import os
                os.lseek(fd, 0, os.SEEK_SET)
                _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
            except OSError:
                pass  # best-effort unlock; fd closes immediately after

else:
    import fcntl as _fcntl

    @contextlib.contextmanager
    def _exclusive_lock(fd: int):
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        try:
            yield
        finally:
            _fcntl.flock(fd, _fcntl.LOCK_UN)


@dataclass
class Session:
    project: Optional[dict] = None
    project_path: Optional[str] = None
    _modified: bool = False
    _undo_stack: list[dict] = field(default_factory=list)
    _redo_stack: list[dict] = field(default_factory=list)

    def has_project(self) -> bool:
        return self.project is not None

    def set_project(self, project: dict, path: Optional[str] = None) -> None:
        self.project = project
        self.project_path = str(path) if path is not None else None
        self._modified = False
        self._undo_stack.clear()
        self._redo_stack.clear()

    def get_project(self) -> dict:
        if not self.project:
            raise RuntimeError("No project loaded")
        return self.project

    def snapshot(self, reason: str) -> None:
        if not self.project:
            raise RuntimeError("No project loaded")
        self._undo_stack.append(copy.deepcopy(self.project))
        self._redo_stack.clear()
        self._modified = True
        project_mod.add_history(self.project, "snapshot", {"reason": reason})

    def mark_dirty(self) -> None:
        if not self.project:
            raise RuntimeError("No project loaded")
        self._modified = True

    def undo(self) -> dict:
        if not self._undo_stack:
            raise RuntimeError("Nothing to undo")
        if self.project:
            self._redo_stack.append(copy.deepcopy(self.project))
        self.project = self._undo_stack.pop()
        self._modified = True
        return self.project

    def redo(self) -> dict:
        if not self._redo_stack:
            raise RuntimeError("Nothing to redo")
        if self.project:
            self._undo_stack.append(copy.deepcopy(self.project))
        self.project = self._redo_stack.pop()
        self._modified = True
        return self.project

    def status(self) -> dict:
        return {
            "has_project": self.has_project(),
            "project_path": self.project_path,
            "modified": self._modified,
            "undo_depth": len(self._undo_stack),
            "redo_depth": len(self._redo_stack),
        }

    def _locked_save_json(self, target: Path, data: dict) -> None:
        # target is pre-validated by project.safe_target(); the cwd re-check
        # below is defense-in-depth so the open() can never escape the
        # working directory even if a future caller skips validation.
        import os
        allowed = Path.cwd().resolve()
        resolved = target.resolve()
        if not resolved.is_relative_to(allowed):
            raise ValueError(f"State file path escapes working directory: {target}")
        lock_path = str(target) + ".lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            with _exclusive_lock(lock_fd):
                tmp_path = str(target) + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, str(target))
        finally:
            os.close(lock_fd)

    def save_session(self, path: Optional[str] = None) -> str:
        if not self.project:
            raise RuntimeError("No project loaded")
        raw = path or self.project_path
        if not raw:
            raise RuntimeError("No project path set")
        target = project_mod.safe_target(raw)
        self.project["updated_at"] = project_mod.utc_now()
        self._locked_save_json(target, self.project)
        # keep the caller's original (relative) name — re-saving the resolved
        # absolute path here would fail safe_target on the next save
        self.project_path = str(raw)
        self._modified = False
        return str(target)

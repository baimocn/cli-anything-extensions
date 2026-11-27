#!/usr/bin/env python3
"""cli-anything-doubao — a Doubao desktop harness CLI (CDP control surface).

Closed loop:
    status            -> CDP endpoint + chat page + editor health
    chat list|new|open-> the sidebar conversation panel
    ask <prompt>      -> send and wait for the finished answer
    read              -> the latest answer
    history           -> the open conversation transcript
    screenshot -o P   -> page capture (background)
    eval <js>         -> one-off page JS passthrough

All output is available in a stable JSON envelope via ``--json``.
"""

from __future__ import annotations

import os
import shlex
import sys
from typing import Optional

import click

from cli_anything.doubao.core import project as project_mod
from cli_anything.doubao.core import doubao as doubao_mod
from cli_anything.doubao.core.session import Session
from cli_anything.doubao.utils import doubao_backend as backend
from cli_anything.doubao.utils.repl_skin import ReplSkin

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
VERSION = "1.0.0"

_session: Optional[Session] = None
_json_output = False
_repl_mode = False


def get_session() -> Session:
    global _session
    if _session is None:
        _session = Session()
    return _session


# ── JSON contract ───────────────────────────────────────────────────────────

def _json_envelope(ok: bool, command: str, data=None, error: Exception | None = None):
    payload = {"ok": ok, "command": command, "data": data, "error": None}
    if error is not None:
        payload["error"] = {"type": type(error).__name__, "message": str(error)}
    return payload


def output(data, message: str = "", command: str = ""):
    if _json_output:
        click.echo(
            json_dumps(_json_envelope(True, command or "unknown", data=data))
        )
        return
    if message:
        click.echo(message)
    if isinstance(data, dict):
        for k, v in data.items():
            click.echo(f"{k}: {v}")
    elif isinstance(data, list):
        for item in data:
            click.echo(item)
    elif data is not None:
        click.echo(data)


def json_dumps(payload) -> str:
    import json
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _emit_error(err: Exception, command: str = "unknown"):
    if _json_output:
        click.echo(json_dumps(_json_envelope(False, command, data=None, error=err)))
    else:
        click.echo(f"Error: {err}", err=True)


def handle_error(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (RuntimeError, ValueError, FileNotFoundError, IndexError) as e:
            _emit_error(
                e,
                command=func.__name__.removesuffix("_cmd").replace("_", ".", 1),
            )
            if not _repl_mode:
                sys.exit(1)
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


# ── Config resolution ───────────────────────────────────────────────────────

def _backend_config(ctx: click.Context) -> backend.BackendConfig:
    """Resolve config: CLI flags > loaded project > env/defaults."""
    port = ctx.obj.get("port")
    app_path = ctx.obj.get("app_path")
    sess = get_session()
    if sess.has_project():
        backend_cfg = (sess.get_project().get("backend") or {})
        if port is None:
            port = backend_cfg.get("port")
    return backend.resolve_config(port, app_path)


# ── Root group ──────────────────────────────────────────────────────────────

@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Emit machine-readable JSON")
@click.option("--project", "project_path", type=str, default=None,
              help="Harness project JSON path (single filename in cwd)")
@click.option("--port", type=int, default=None, envvar="DOUBAO_CDP_PORT",
              help="Doubao CDP debug port (default 9333)")
@click.option("--app-path", type=str, default=None, envvar="DOUBAO_APP_PATH",
              help="Doubao.exe path for --launch auto-start")
@click.option("--launch", "launch", is_flag=True, default=False,
              help="Auto-start Doubao with the debug port if not running")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Do not auto-save project state on exit")
@click.version_option(version=VERSION, prog_name="cli-anything-doubao")
@click.pass_context
def cli(ctx, use_json, project_path, port, app_path, launch, dry_run):
    """Stateful Doubao CLI harness backed by the Chrome DevTools Protocol."""
    global _json_output
    _json_output = use_json

    sess = get_session()
    if project_path and not sess.has_project() and os.path.exists(project_path):
        sess.set_project(
            project_mod.open_project(project_path),
            project_mod.safe_target(project_path),
        )

    ctx.ensure_object(dict)
    ctx.obj["port"] = port
    ctx.obj["app_path"] = app_path
    ctx.obj["launch"] = launch
    ctx.obj["dry_run"] = dry_run

    if launch:
        with_click_suppress(backend.ensure_app(_backend_config(ctx)))

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


def with_click_suppress(result):
    if isinstance(result, dict) and result.get("launched"):
        click.echo(f"Doubao launched (port {result.get('port')})", err=True)


@cli.result_callback()
def auto_save_on_exit(result, use_json, project_path, port, app_path, launch,
                      dry_run, **kwargs):
    if _repl_mode or dry_run:
        return
    sess = get_session()
    if sess.has_project() and sess._modified and sess.project_path:
        try:
            sess.save_session()
        except Exception as e:  # pragma: no cover - best effort
            click.echo(f"Warning: auto-save failed: {e}", err=True)


# ── REPL ────────────────────────────────────────────────────────────────────

@cli.command("repl", hidden=True)
@click.pass_context
def repl(ctx):
    global _repl_mode
    _repl_mode = True
    skin = ReplSkin("doubao", version=VERSION)
    skin.print_banner()
    skin.info("Type 'help' for commands, 'exit' to quit.")
    pt_session = skin.create_prompt_session()

    while True:
        try:
            line = skin.get_input(
                pt_session,
                project_name="doubao-harness",
                modified=get_session()._modified,
            )
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            skin.help({
                "status": "CDP endpoint + chat page + editor health",
                "chat list": "Sidebar conversations",
                "chat new": "Start a fresh conversation",
                "chat open <n>": "Open sidebar conversation N",
                "ask <prompt>": "Send and wait for the finished answer",
                "read": "Show the latest answer",
                "history [-n N]": "Transcript of the open conversation",
                "screenshot -o PATH": "Capture the page (background)",
                "eval <js>": "One-off page JS passthrough",
                "project new|open|save|info": "Harness project state",
                "state undo|redo|status": "Session controls",
            })
            continue
        try:
            args = shlex.split(line)
            cli.main(args=args, obj=dict(ctx.obj), standalone_mode=False)
        except click.exceptions.UsageError as e:
            skin.error(str(e))
        except SystemExit:
            pass
        except Exception as e:
            skin.error(str(e))

    skin.print_goodbye()


# ── status ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--launch", "launch", is_flag=True, default=False,
              help="Auto-start Doubao if the CDP endpoint is down")
@click.pass_context
@handle_error
def status(ctx, launch):
    """Health of the control surface (CDP endpoint, chat page, editor)."""
    data = doubao_mod.status(_backend_config(ctx), launch=launch)
    output(data, message=(
        f"cdp: {'OK' if data['cdp_alive'] else 'DOWN'} | "
        f"page: {'OK' if data['page'].get('found') else 'NOT FOUND'} | "
        f"editor: {'OK' if data['input'].get('editor_ready') else '?'}"
    ), command="status")


# ── chat ────────────────────────────────────────────────────────────────────

@cli.group()
def chat():
    """Sidebar conversation operations."""


@chat.command("list")
@click.pass_context
@handle_error
def chat_list(ctx):
    """List sidebar conversations (index, title)."""
    data = doubao_mod.list_conversations(_backend_config(ctx))
    output(data, message=f"{len(data)} conversation(s)", command="chat.list")


@chat.command("new")
@click.pass_context
@handle_error
def chat_new(ctx):
    """Start a fresh conversation."""
    data = doubao_mod.new_chat(_backend_config(ctx))
    output(data, message="New conversation" if data["clicked"] else "Button not found",
           command="chat.new")


@chat.command("open")
@click.argument("index", type=int)
@click.pass_context
@handle_error
def chat_open(ctx, index):
    """Open sidebar conversation INDEX."""
    data = doubao_mod.open_conversation(index, _backend_config(ctx))
    output(data, message=f"Opened #{index}" if data["opened"] else f"#{index} not found",
           command="chat.open")


# ── ask / read / history / screenshot / eval ────────────────────────────────

@cli.command()
@click.argument("prompt_text")
@click.option("--timeout", default=90.0, show_default=True,
              help="Seconds to wait for the answer to finish")
@click.pass_context
@handle_error
def ask(ctx, prompt_text, timeout):
    """Send PROMPT_TEXT and wait for Doubao's finished answer."""
    data = doubao_mod.ask_and_wait(prompt_text, timeout=timeout,
                                   config=_backend_config(ctx))
    sess = get_session()
    if sess.has_project():
        sess.snapshot("ask")
        sess.get_project().setdefault("context", {})["last_answer"] = (
            data.get("answer") or ""
        )[:200]
    if _json_output:
        output(data, command="ask")
        return
    mark = "OK" if data.get("round_trip_ok") else "TIMEOUT"
    click.echo(f"[{mark}] {data.get('answer')}")


@cli.command()
@click.pass_context
@handle_error
def read(ctx):
    """Show the latest assistant answer in the open conversation."""
    data = doubao_mod.read_answer(_backend_config(ctx))
    output(data, message=f"Answer: {data.get('last_answer')}", command="read")


@cli.command()
@click.option("-n", "--limit", default=40, show_default=True)
@click.pass_context
@handle_error
def history(ctx, limit):
    """Transcript of the open conversation (oldest-first)."""
    data = doubao_mod.read_conversation(limit, _backend_config(ctx))
    if _json_output:
        output(data, command="history")
        return
    click.echo(f"Transcript ({data['count']} messages, oldest first):")
    for m in data["messages"]:
        click.echo(f"[{m['role']}] {m['text']}")


@cli.command()
@click.option("-o", "--out", "out_path", required=True, help="Destination PNG")
@click.pass_context
@handle_error
def screenshot(ctx, out_path):
    """Capture the chat page to OUT_PATH (fully background)."""
    data = doubao_mod.screenshot(out_path, _backend_config(ctx))
    output(data, message=f"Saved {data['path']} ({data['bytes']} bytes)",
           command="screenshot")


@cli.command("eval")
@click.argument("expression")
@click.pass_context
@handle_error
def eval_cmd(ctx, expression):
    """Evaluate page JS (one-off DOM probe; escape hatch)."""
    data = doubao_mod.raw_eval(expression, _backend_config(ctx))
    output(data, command="eval")


# ── project / state (local only) ────────────────────────────────────────────

@cli.group("project")
def project():
    """Harness project state (local, no network)."""


@project.command("new")
@click.option("--name", default="doubao-harness", show_default=True)
@click.option("-o", "--output", "out_path", default="doubao-harness.json",
              show_default=True, help="Project filename in cwd")
@click.pass_context
@handle_error
def project_new(ctx, name, out_path):
    """Create a new project state file."""
    cfg = _backend_config(ctx)
    proj = project_mod.create_project(name, port=cfg.port)
    saved = project_mod.save_project(proj, out_path)
    get_session().set_project(proj, out_path)
    output({"path": saved, **project_mod.project_info(proj)},
           message=f"Created project {saved}", command="project.new")


@project.command("open")
@click.argument("path")
@click.pass_context
@handle_error
def project_open(ctx, path):
    """Open an existing project state file."""
    proj = project_mod.open_project(path)  # validates the raw name
    get_session().set_project(proj, path)
    output(project_mod.project_info(proj), message=f"Opened {path}",
           command="project.open")


@project.command("save")
@click.pass_context
@handle_error
def project_save(ctx):
    """Persist the current project state."""
    sess = get_session()
    path = sess.save_session()
    output({"path": path}, message=f"Saved {path}", command="project.save")


@project.command("info")
@click.pass_context
@handle_error
def project_info_cmd(ctx):
    """Show current project info."""
    output(project_mod.project_info(get_session().get_project()), command="project.info")


@cli.group("state")
def state():
    """Session undo / redo controls."""


@state.command("status")
@click.pass_context
def state_status(ctx):
    """Show session status."""
    output(get_session().status(), command="state.status")


@state.command("undo")
@click.pass_context
@handle_error
def state_undo(ctx):
    """Undo the last state change."""
    output(get_session().undo(), command="state.undo")


@state.command("redo")
@click.pass_context
@handle_error
def state_redo(ctx):
    """Redo the last undone change."""
    output(get_session().redo(), command="state.redo")


def main():
    cli(obj={})


if __name__ == "__main__":
    main()

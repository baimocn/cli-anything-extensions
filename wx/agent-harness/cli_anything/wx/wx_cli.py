#!/usr/bin/env python3
"""cli-anything-wx — a WeChat PC 4.x harness CLI.

Two channels, one contract:
    status            -> decrypted-DB health + client window health
    session list      -> session list (contacts panel)
    contact search    -> contact lookup
    group list|members-> groups / group members
    msg history|new   -> read a conversation (oldest-first, agent-readable)
    msg chats|export  -> chats holding messages / full-fidelity export
    send text|file    -> UIA send through the real client
    msg recall / poke / chat open -> client actions (UIA)
    roundtrip         -> send then prove delivery via DB read-back
    db                -> read-only WeChatDB passthrough (whitelisted)

All output is available in a stable JSON envelope via ``--json``.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from typing import Optional

import click

from cli_anything.wx.core import project as project_mod
from cli_anything.wx.core import wx as wx_mod
from cli_anything.wx.core.session import Session
from cli_anything.wx.utils import wx_backend as backend
from cli_anything.wx.utils.repl_skin import ReplSkin

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
            json.dumps(
                _json_envelope(True, command or "unknown", data=data),
                indent=2,
                ensure_ascii=False,
                default=str,
            )
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


def _emit_error(err: Exception, command: str = "unknown"):
    if _json_output:
        click.echo(json.dumps(_json_envelope(False, command, data=None, error=err),
                              ensure_ascii=False))
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
    db_dir = ctx.obj.get("db_dir")
    account = ctx.obj.get("account")
    win_manage = ctx.obj.get("window_manage")
    hide_after = ctx.obj.get("hide_after")
    sess = get_session()
    if sess.has_project():
        backend_cfg = (sess.get_project().get("backend") or {})
        if db_dir is None:
            db_dir = backend_cfg.get("db_dir")
        if account is None:
            account = backend_cfg.get("account")
    if win_manage is None:
        win_manage = True
    if hide_after is None:
        hide_after = False
    return backend.resolve_config(db_dir, account, win_manage, hide_after)


# ── Root group ──────────────────────────────────────────────────────────────

@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Emit machine-readable JSON")
@click.option("--project", "project_path", type=str, default=None,
              help="Harness project JSON path (single filename in cwd)")
@click.option("--db-dir", type=str, default=None, envvar="WX_DB_DIR",
              help="Override the WeChat data directory (default: auto-detect)")
@click.option("--account", type=str, default=None, envvar="WX_ACCOUNT",
              help="Account subdirectory under the data dir (default: auto)")
@click.option("--no-window-management", "no_win_manage", is_flag=True, default=False,
              help="Never restore/topmost the client window (headless)")
@click.option("--hide-after", "hide_after", is_flag=True, default=False,
              envvar="WX_HIDE_AFTER_SEND",
              help="Minimize the client window back to the tray after each send")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Do not auto-save project state on exit")
@click.version_option(version=VERSION, prog_name="cli-anything-wx")
@click.pass_context
def cli(ctx, use_json, project_path, db_dir, account, no_win_manage, hide_after, dry_run):
    """Stateful WeChat 4.x CLI harness: decrypted-DB reading + UIA sending."""
    global _json_output
    _json_output = use_json

    sess = get_session()
    if project_path and not sess.has_project() and os.path.exists(project_path):
        sess.set_project(
            project_mod.open_project(project_path),
            project_mod.safe_target(project_path),
        )

    ctx.ensure_object(dict)
    ctx.obj["db_dir"] = db_dir
    ctx.obj["account"] = account
    ctx.obj["window_manage"] = not no_win_manage
    ctx.obj["hide_after"] = hide_after
    ctx.obj["dry_run"] = dry_run

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.result_callback()
def auto_save_on_exit(result, use_json, project_path, db_dir, account,
                      no_win_manage, hide_after, dry_run, **kwargs):
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
    skin = ReplSkin("wx", version=VERSION)
    skin.print_banner()
    skin.info("Type 'help' for commands, 'exit' to quit.")
    pt_session = skin.create_prompt_session()

    while True:
        try:
            line = skin.get_input(
                pt_session,
                project_name="wx-harness",
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
                "status": "DB + window health for the WeChat control surface",
                "session list": "Session list (the contacts panel)",
                "contact search <kw>": "Search contacts",
                "group list|members": "Groups / group members",
                "msg history <who>": "Read a conversation (oldest-first)",
                "msg new <who> --since SEQ": "Incremental read by sort_seq",
                "msg chats": "Chats holding messages in the local DB",
                "msg export <who|all> -o PATH": "Full-fidelity export",
                "send text <who> <msg>": "Send via the real client (UIA)",
                "send file <who> <path>": "Send a file via the real client",
                "msg recall <who>": "Recall the last sent message",
                "poke <who>": "Poke (拍一拍) a chat",
                "chat open <who>": "Open a chat window",
                "roundtrip <who> <msg>": "Send then verify via DB read-back",
                "db <method> --kwargs '{}'": "Read-only WeChatDB passthrough",
                "project new|open|save|info": "Harness project state",
                "session undo|redo|status": "Session controls",
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
@click.pass_context
@handle_error
def status(ctx):
    """Health of both channels: decrypted DB + client window."""
    data = wx_mod.status(_backend_config(ctx))
    db_ok = data["db"].get("decryptable")
    win_ok = data["window"].get("available")
    output(data, message=(
        f"db: {'OK' if db_ok else 'FAIL'} | "
        f"window: {'OK' if win_ok else 'UNAVAILABLE'}"
    ), command="status")


# ── session / contact / group ───────────────────────────────────────────────

@cli.group()
def session():
    """Session list and session-state controls (see `session state`)."""


@session.command("list")
@click.option("-n", "--limit", default=100, show_default=True)
@click.pass_context
@handle_error
def session_list(ctx, limit):
    """List recent sessions (the GUI's session panel)."""
    data = wx_mod.list_sessions(limit, _backend_config(ctx))
    output(data, message=f"{len(data)} session(s)", command="session.list")


@cli.group("contact")
def contact():
    """Contact operations (read-only, from the decrypted DB)."""


@contact.command("search")
@click.argument("keyword")
@click.pass_context
@handle_error
def contact_search(ctx, keyword):
    """Search contacts by KEYWORD (nickname/remark fragment)."""
    data = wx_mod.search_contacts(keyword, _backend_config(ctx))
    output(data, message=f"{len(data)} hit(s)", command="contact.search")


@cli.group()
def group():
    """Group operations (read-only)."""


@group.command("list")
@click.pass_context
@handle_error
def group_list(ctx):
    """List groups."""
    data = wx_mod.list_groups(_backend_config(ctx))
    output(data, message=f"{len(data)} group(s)", command="group.list")


@group.command("members")
@click.argument("group_id")
@click.pass_context
@handle_error
def group_members(ctx, group_id):
    """List members of group GROUP_ID (wxid or display name)."""
    data = wx_mod.list_group_members(group_id, _backend_config(ctx))
    output(data, message=f"{len(data)} member(s)", command="group.members")


# ── msg ─────────────────────────────────────────────────────────────────────

@cli.group()
def msg():
    """Message operations (read from the decrypted DB; actions via UIA)."""


@msg.command("history")
@click.argument("who")
@click.option("-n", "--count", default=20, show_default=True, help="Max messages")
@click.option("--offset", default=0, show_default=True, help="Rows to skip")
@click.pass_context
@handle_error
def msg_history(ctx, who, count, offset):
    """Read recent history with WHO (oldest-first, agent-readable)."""
    data = wx_mod.read_history(who, count, offset, _backend_config(ctx))
    if _json_output:
        output(data, command="msg.history")
        return
    key = data.get("key") or "(no rows)"
    click.echo(f"History with {who} (key={key}, {data['count']} messages, oldest first):")
    for m in data["messages"]:
        who_txt = "self" if m["is_self"] else m["sender"]
        click.echo(f"[{m['time']}] {who_txt}: {m['text']}")


@msg.command("new")
@click.argument("who")
@click.option("--since", "since_seq", required=True, type=int,
              help="Only rows with sort_seq > SINCE (poll watermark)")
@click.option("-n", "--limit", default=200, show_default=True)
@click.pass_context
@handle_error
def msg_new(ctx, who, since_seq, limit):
    """Incremental read: messages newer than the SINCE watermark."""
    data = wx_mod.read_new(who, since_seq, limit, _backend_config(ctx))
    output(data, message=f"{data['count']} new message(s)", command="msg.new")


@msg.command("chats")
@click.pass_context
@handle_error
def msg_chats(ctx):
    """List chats that hold messages in the local DB."""
    data = wx_mod.list_message_chats(_backend_config(ctx))
    output(data, message=f"{len(data)} chat(s) with messages", command="msg.chats")


@msg.command("export")
@click.argument("who")
@click.option("-o", "--out", "out_path", required=True, help="Destination file")
@click.option("-n", "--limit", "limit_per_chat", default=None, type=int,
              help="Max messages per chat (default: all)")
@click.option("--fmt", "fmt", type=click.Choice(["json", "sqlite"]), default="json",
              show_default=True)
@click.pass_context
@handle_error
def msg_export(ctx, who, out_path, limit_per_chat, fmt):
    """Export history for WHO (or 'all') to OUT_PATH."""
    target = project_mod.safe_export_path(out_path)
    users = None if who == "all" else [who]
    data = wx_mod.export_history(
        str(target), users=users, limit_per_chat=limit_per_chat,
        fmt=fmt, config=_backend_config(ctx),
    )
    output(data, message=f"Exported to {target}", command="msg.export")


@msg.command("recall")
@click.argument("who")
@click.pass_context
@handle_error
def msg_recall(ctx, who):
    """Recall the last message this account sent in chat WHO (UIA)."""
    data = wx_mod.recall_last(who, _backend_config(ctx))
    output(data, message=f"Recall result: {data['status']}", command="msg.recall")


# ── send ────────────────────────────────────────────────────────────────────

@cli.group()
def send():
    """Send through the real client (UIA)."""


@send.command("text")
@click.argument("who")
@click.argument("text")
@click.option("--verify", is_flag=True, default=False,
              help="Use the library's compose read-back gate")
@click.pass_context
@handle_error
def send_text(ctx, who, text, verify):
    """Send TEXT to chat WHO (display name, remark or wxid)."""
    data = wx_mod.send_text(who, text, verify=verify, config=_backend_config(ctx))
    sess = get_session()
    if sess.has_project():
        sess.snapshot("send.text")
        sess.get_project().setdefault("context", {})["last_chat"] = who
    output(data, message=f"Send status: {data['status']}", command="send.text")


@send.command("file")
@click.argument("who")
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
@handle_error
def send_file(ctx, who, file):
    """Send a local FILE to chat WHO."""
    data = wx_mod.send_file(who, file, _backend_config(ctx))
    output(data, message=f"Send status: {data['status']}", command="send.file")


# ── poke / chat / label ─────────────────────────────────────────────────────

@cli.command()
@click.argument("who")
@click.pass_context
@handle_error
def poke(ctx, who):
    """Poke (拍一拍) in chat WHO."""
    data = wx_mod.poke(who, _backend_config(ctx))
    output(data, message=f"Poke status: {data['status']}", command="poke")


@cli.command("chat")
@click.argument("who")
@click.pass_context
@handle_error
def chat_open(ctx, who):
    """Open the chat window for WHO and bring it to front."""
    data = wx_mod.chat_open(who, _backend_config(ctx))
    output(data, message=f"Opened {who}", command="chat.open")


@cli.command("label")
@click.pass_context
@handle_error
def label_list(ctx):
    """List contact labels (tags)."""
    data = backend.list_labels(_backend_config(ctx))
    output(data, message=f"{len(data)} label(s)", command="label.list")


# ── roundtrip (the closed-loop mechanism) ──────────────────────────────────

@cli.command()
@click.argument("who")
@click.argument("text")
@click.option("--timeout", default=12.0, show_default=True,
              help="Seconds to wait for the DB read-back")
@click.pass_context
@handle_error
def roundtrip(ctx, who, text, timeout):
    """Send TEXT to WHO then prove delivery via the decrypted DB."""
    data = wx_mod.send_and_verify(who, text, timeout=timeout, config=_backend_config(ctx))
    output(data, message=(
        f"Round-trip {'OK' if data['round_trip_ok'] else 'NOT CONFIRMED'} "
        f"(key={data.get('key')})"
    ), command="roundtrip")


# ── db: read-only passthrough ──────────────────────────────────────────────

@cli.command("db")
@click.argument("method")
@click.option("--kwargs", "kwargs_json", default="{}",
              help="Method kwargs as JSON, e.g. --kwargs '{\"user\": \"filehelper\"}'")
@click.pass_context
@handle_error
def db_passthrough(ctx, method, kwargs_json):
    """Invoke a whitelisted read-only WeChatDB METHOD."""
    try:
        kwargs = json.loads(kwargs_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid --kwargs JSON: {e}") from e
    if not isinstance(kwargs, dict):
        raise ValueError("--kwargs must be a JSON object")
    data = wx_mod.db_passthrough(method, kwargs, _backend_config(ctx))
    output(data, command=f"db.{method}")


# ── project / session state (local only) ───────────────────────────────────

@cli.group("project")
def project():
    """Harness project state (local, no network)."""


@project.command("new")
@click.option("--name", default="wx-harness", show_default=True)
@click.option("-o", "--output", "out_path", default="wx-harness.json",
              show_default=True, help="Project filename in cwd")
@click.pass_context
@handle_error
def project_new(ctx, name, out_path):
    """Create a new project state file."""
    cfg = _backend_config(ctx)
    proj = project_mod.create_project(name, db_dir=cfg.db_dir, account=cfg.account)
    # save_project validates the raw relative name itself; never feed it the
    # already-resolved absolute path back through its own guard.
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

#!/usr/bin/env python3
"""cli-anything-qq — a OneBot 11 / NapCat harness CLI for QQ.

Minimal closed-loop v1:
    status            -> get_login_info (connectivity + identity)
    send private|group -> send_private_msg / send_group_msg (returns message_id)
    msg get <id>       -> get_msg (read back)
    roundtrip          -> send then read back in one command

All output is available in a stable JSON envelope via ``--json``.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from typing import Optional

import click
import requests

from cli_anything.qq.core import project as project_mod
from cli_anything.qq.core import qq as qq_mod
from cli_anything.qq.core.session import Session
from cli_anything.qq.utils.qq_backend import BackendConfig, resolve_config
from cli_anything.qq.utils.repl_skin import ReplSkin

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
        except (RuntimeError, ValueError, FileNotFoundError, IndexError,
                requests.exceptions.RequestException) as e:
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

def _backend_config(ctx: click.Context) -> BackendConfig:
    """Resolve config: CLI flags > loaded project > env/defaults.

    The ``--url``/``--token`` options default to None so that an omitted flag
    does not override values persisted in the project.
    """
    url = ctx.obj.get("url")
    token = ctx.obj.get("token")
    sess = get_session()
    if sess.has_project():
        backend = (sess.get_project().get("backend") or {})
        if url is None:
            url = backend.get("base_url")
        if token is None:
            token = backend.get("access_token")
    return resolve_config(url, token)


# ── Root group ──────────────────────────────────────────────────────────────

@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Emit machine-readable JSON")
@click.option("--project", "project_path", type=str, default=None,
              help="Harness project JSON path (single filename in cwd)")
@click.option("--url", type=str, default=None, envvar="QQ_NAPCAT_URL",
              help="NapCat OneBot HTTP endpoint")
@click.option("--token", type=str, default=None, envvar="QQ_NAPCAT_TOKEN",
              help="OneBot access token (Bearer)")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Do not auto-save project state on exit")
@click.version_option(version=VERSION, prog_name="cli-anything-qq")
@click.pass_context
def cli(ctx, use_json, project_path, url, token, dry_run):
    """Stateful QQ CLI harness backed by the NapCat OneBot 11 HTTP API."""
    global _json_output
    _json_output = use_json

    sess = get_session()
    if project_path and not sess.has_project() and os.path.exists(project_path):
        sess.set_project(
            project_mod.open_project(project_path),
            project_mod.safe_target(project_path),
        )

    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["token"] = token
    ctx.obj["dry_run"] = dry_run

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.result_callback()
def auto_save_on_exit(result, use_json, project_path, url, token, dry_run, **kwargs):
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
    skin = ReplSkin("qq", version=VERSION)
    skin.print_banner()
    skin.info("Type 'help' for commands, 'exit' to quit.")
    pt_session = skin.create_prompt_session()

    while True:
        try:
            line = skin.get_input(
                pt_session,
                project_name="qq-harness",
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
                "status": "Check NapCat connectivity and login identity",
                "friend list": "List friends (contacts)",
                "group list|members": "List groups / group members",
                "send private <uid> <msg>": "Send a private message",
                "send group <gid> <msg>": "Send a group message",
                "msg get <message_id>": "Read back a message by id",
                "msg history private|group <id>": "Read recent chat history",
                "roundtrip private|group <id> <msg>": "Send then read back",
                "project new|open|save|info|status": "Harness project state",
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
    """Check connectivity and show the logged-in QQ account."""
    data = qq_mod.login_status(_backend_config(ctx))
    output(data, message=f"Connected as {data['nickname']} ({data['user_id']})",
           command="status")


# ── send ────────────────────────────────────────────────────────────────────

@cli.group()
def send():
    """Send a message."""


@send.command("private")
@click.argument("user_id")
@click.argument("message")
@click.pass_context
@handle_error
def send_private(ctx, user_id, message):
    """Send a private message to USER_ID."""
    data = qq_mod.send_private(user_id, message, _backend_config(ctx))
    sess = get_session()
    if sess.has_project():
        sess.get_project().setdefault("context", {})["last_message_id"] = data.get("message_id")
        sess.mark_dirty()
    output(data, message=f"Sent private message (id={data['message_id']})",
           command="send.private")


@send.command("group")
@click.argument("group_id")
@click.argument("message")
@click.pass_context
@handle_error
def send_group(ctx, group_id, message):
    """Send a group message to GROUP_ID."""
    data = qq_mod.send_group(group_id, message, _backend_config(ctx))
    sess = get_session()
    if sess.has_project():
        sess.get_project().setdefault("context", {})["last_message_id"] = data.get("message_id")
        sess.mark_dirty()
    output(data, message=f"Sent group message (id={data['message_id']})",
           command="send.group")


# ── msg ─────────────────────────────────────────────────────────────────────

@cli.group()
def msg():
    """Message operations."""


@msg.command("get")
@click.argument("message_id")
@click.pass_context
@handle_error
def msg_get(ctx, message_id):
    """Read back a message by its MESSAGE_ID."""
    data = qq_mod.read_message(message_id, _backend_config(ctx))
    output(data, message=f"Message {data['message_id']}: {data['message']}",
           command="msg.get")


@msg.command("history")
@click.argument("target", type=click.Choice(["private", "group"]))
@click.argument("peer_id")
@click.option("-n", "--count", default=20, show_default=True, help="Max messages")
@click.pass_context
@handle_error
def msg_history(ctx, target, peer_id, count):
    """Read recent chat history with a peer (oldest-first, agent-readable)."""
    data = qq_mod.read_history(target, peer_id, count, _backend_config(ctx))
    if _json_output:
        output(data, command="msg.history")
        return
    click.echo(f"History with {target} {peer_id} ({data['count']} messages, oldest first):")
    for m in data["messages"]:
        click.echo(f"[{m['time']}] {m['sender']}({m['sender_id']}): {m['text']}")


# ── friend / group (v2: replace the GUI's contact panel) ───────────────

@cli.group()
def friend():
    """Friend (contact) operations."""


@friend.command("list")
@click.pass_context
@handle_error
def friend_list(ctx):
    """List friends (id, nickname, remark)."""
    data = qq_mod.list_friends(_backend_config(ctx))
    output(data, message=f"{len(data)} friend(s)", command="friend.list")


@cli.group()
def group():
    """Group operations."""


@group.command("list")
@click.pass_context
@handle_error
def group_list(ctx):
    """List groups (id, name, member count)."""
    data = qq_mod.list_groups(_backend_config(ctx))
    output(data, message=f"{len(data)} group(s)", command="group.list")


@group.command("members")
@click.argument("group_id")
@click.pass_context
@handle_error
def group_members(ctx, group_id):
    """List members of GROUP_ID."""
    data = qq_mod.list_group_members(group_id, _backend_config(ctx))
    output(data, message=f"{len(data)} member(s)", command="group.members")


@group.command("info")
@click.argument("group_id")
@click.pass_context
@handle_error
def group_info(ctx, group_id):
    """Show GROUP_ID details."""
    data = qq_mod.group_info(group_id, _backend_config(ctx))
    output(data, command="group.info")


@group.command("ban")
@click.argument("group_id")
@click.argument("user_id")
@click.option("-d", "--duration", default=600, show_default=True,
              help="Mute duration in seconds")
@click.pass_context
@handle_error
def group_ban(ctx, group_id, user_id, duration):
    """Mute USER_ID in GROUP_ID (requires admin)."""
    qq_mod.group_ban(group_id, user_id, duration, _backend_config(ctx))
    output({"group_id": int(group_id), "user_id": int(user_id), "duration": duration},
           message=f"Muted {user_id} for {duration}s", command="group.ban")


@group.command("whole-ban")
@click.argument("group_id")
@click.option("--off", "enable", flag_value=False, default=True,
              help="Lift the whole-group mute")
@click.pass_context
@handle_error
def group_whole_ban(ctx, group_id, enable):
    """Toggle whole-group mute for GROUP_ID."""
    qq_mod.group_whole_ban(group_id, enable, _backend_config(ctx))
    state = "enabled" if enable else "disabled"
    output({"group_id": int(group_id), "whole_ban": enable},
           message=f"Whole-group mute {state}", command="group.whole-ban")


@group.command("kick")
@click.argument("group_id")
@click.argument("user_id")
@click.option("--reject-rejoin", is_flag=True, help="Also block re-entry")
@click.pass_context
@handle_error
def group_kick(ctx, group_id, user_id, reject_rejoin):
    """Remove USER_ID from GROUP_ID (destructive)."""
    qq_mod.group_kick(group_id, user_id, reject_rejoin, _backend_config(ctx))
    output({"group_id": int(group_id), "user_id": int(user_id), "kicked": True},
           message=f"Kicked {user_id} from {group_id}", command="group.kick")


@group.command("card")
@click.argument("group_id")
@click.argument("user_id")
@click.argument("card", default="")
@click.pass_context
@handle_error
def group_card(ctx, group_id, user_id, card):
    """Set (or clear with empty string) USER_ID's card in GROUP_ID."""
    qq_mod.group_card(group_id, user_id, card, _backend_config(ctx))
    output({"group_id": int(group_id), "user_id": int(user_id), "card": card},
           message=f"Card set to '{card or '(cleared)'}'", command="group.card")


@group.command("admin")
@click.argument("group_id")
@click.argument("user_id")
@click.option("--off", "enable", flag_value=False, default=True,
              help="Revoke the admin role")
@click.pass_context
@handle_error
def group_admin(ctx, group_id, user_id, enable):
    """Grant/revoke admin for USER_ID in GROUP_ID (requires owner)."""
    qq_mod.group_admin(group_id, user_id, enable, _backend_config(ctx))
    output({"group_id": int(group_id), "user_id": int(user_id), "admin": enable},
           message=f"Admin {'granted to' if enable else 'revoked from'} {user_id}",
           command="group.admin")


@group.command("title")
@click.argument("group_id")
@click.argument("user_id")
@click.argument("title")
@click.pass_context
@handle_error
def group_title(ctx, group_id, user_id, title):
    """Set USER_ID's special title in GROUP_ID."""
    qq_mod.group_title(group_id, user_id, title, _backend_config(ctx))
    output({"group_id": int(group_id), "user_id": int(user_id), "title": title},
           message=f"Title set to '{title}'", command="group.title")


@group.command("leave")
@click.argument("group_id")
@click.confirmation_option(prompt="This will LEAVE the group. Confirm?")
@click.pass_context
@handle_error
def group_leave(ctx, group_id):
    """Leave GROUP_ID (destructive, asks for confirmation)."""
    qq_mod.raw_action("set_group_leave", {"group_id": int(group_id)},
                      _backend_config(ctx))
    output({"group_id": int(group_id), "left": True},
           message=f"Left group {group_id}", command="group.leave")


@group.command("essence")
@click.argument("group_id")
@click.pass_context
@handle_error
def group_essence(ctx, group_id):
    """List essence (pinned) messages of GROUP_ID."""
    data = qq_mod.group_essence(group_id, _backend_config(ctx))
    output(data, message=f"{len(data)} essence message(s)", command="group.essence")


@group.command("at-all-remain")
@click.argument("group_id")
@click.pass_context
@handle_error
def group_at_all_remain(ctx, group_id):
    """Show remaining @-all quota for GROUP_ID."""
    data = qq_mod.group_at_all_remain(group_id, _backend_config(ctx))
    output(data, command="group.at-all-remain")


@group.command("sign")
@click.argument("group_id")
@click.pass_context
@handle_error
def group_sign(ctx, group_id):
    """Sign in (打卡) GROUP_ID."""
    qq_mod.group_sign(group_id, _backend_config(ctx))
    output({"group_id": int(group_id), "signed": True},
           message=f"Signed in group {group_id}", command="group.sign")


# ── upload / profile / requests ─────────────────────────────────────────

@cli.group()
def upload():
    """Upload files to a private chat or a group."""


@upload.command("private")
@click.argument("user_id")
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
@handle_error
def upload_private(ctx, user_id, file):
    """Upload FILE to USER_ID."""
    data = qq_mod.upload_file("private", user_id, file, _backend_config(ctx))
    output(data, message=f"Uploaded {os.path.basename(file)} to {user_id}",
           command="upload.private")


@upload.command("group")
@click.argument("group_id")
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
@handle_error
def upload_group(ctx, group_id, file):
    """Upload FILE to GROUP_ID."""
    data = qq_mod.upload_file("group", group_id, file, _backend_config(ctx))
    output(data, message=f"Uploaded {os.path.basename(file)} to group {group_id}",
           command="upload.group")


@cli.command("profile-set")
@click.option("--nickname", default=None, help="New nickname")
@click.option("--signature", default=None, help="New personal signature")
@click.pass_context
@handle_error
def profile_set(ctx, nickname, signature):
    """Update the account's nickname / signature."""
    data = qq_mod.set_profile(nickname, signature, _backend_config(ctx))
    output(data, message="Profile updated", command="profile.set")


@cli.group()
def request():
    """Handle incoming friend / group join requests (flag from events)."""


@request.command("friend")
@click.argument("flag")
@click.option("--deny", "approve", flag_value=False, default=True)
@click.option("--remark", default="", help="Friend remark when approving")
@click.pass_context
@handle_error
def request_friend(ctx, flag, approve, remark):
    """Accept/deny a friend request with its FLAG."""
    qq_mod.handle_request(flag, "friend", approve, remark, _backend_config(ctx))
    output({"flag": flag, "approved": approve},
           message="Approved" if approve else "Denied", command="request.friend")


@request.command("group")
@click.argument("flag")
@click.option("--deny", "approve", flag_value=False, default=True)
@click.pass_context
@handle_error
def request_group(ctx, flag, approve):
    """Accept/deny a group join request with its FLAG."""
    qq_mod.handle_request(flag, "group", approve, _backend_config(ctx))
    output({"flag": flag, "approved": approve},
           message="Approved" if approve else "Denied", command="request.group")


# ── call: raw OneBot action passthrough (full action surface) ──────────

@cli.command("call")
@click.argument("action")
@click.option("--params", "params_json", default="{}",
              help="Action params as JSON, e.g. --params '{\"user_id\":123}'")
@click.pass_context
@handle_error
def call(ctx, action, params_json):
    """Invoke any OneBot action the backend supports (raw passthrough)."""
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid --params JSON: {e}") from e
    if not isinstance(params, dict):
        raise ValueError("--params must be a JSON object")
    data = qq_mod.raw_action(action, params, _backend_config(ctx))
    output(data, command=f"call.{action}")


@msg.command("revoke")
@click.argument("message_id")
@click.pass_context
@handle_error
def msg_revoke(ctx, message_id):
    """Recall a message by MESSAGE_ID (needs admin/own message)."""
    qq_mod.revoke_message(message_id, _backend_config(ctx))
    output({"message_id": int(message_id), "revoked": True},
           message=f"Revoked message {message_id}", command="msg.revoke")


@msg.command("mark-read")
@click.argument("message_id")
@click.pass_context
@handle_error
def msg_mark_read(ctx, message_id):
    """Mark MESSAGE_ID as read."""
    qq_mod.mark_read(message_id, _backend_config(ctx))
    output({"message_id": int(message_id), "read": True},
           message=f"Marked {message_id} as read", command="msg.mark-read")


@msg.command("like")
@click.argument("message_id")
@click.argument("emoji_id", default="4")
@click.option("--remove", is_flag=True, help="Remove the reaction instead")
@click.pass_context
@handle_error
def msg_like(ctx, message_id, emoji_id, remove):
    """React to MESSAGE_ID with a QQ face (default 4 = thumbs-up)."""
    qq_mod.emoji_like(message_id, emoji_id, remove, _backend_config(ctx))
    action = "Removed" if remove else "Added"
    output({"message_id": int(message_id), "emoji_id": emoji_id},
           message=f"{action} reaction {emoji_id} on {message_id}",
           command="msg.like")


@msg.command("ocr")
@click.argument("image")
@click.pass_context
@handle_error
def msg_ocr(ctx, image):
    """OCR an image (URL or local path) and return recognized texts."""
    data = qq_mod.ocr_image(image, _backend_config(ctx))
    output(data, command="msg.ocr")


# ── poke ────────────────────────────────────────────────────────────────

@cli.group()
def poke():
    """Poke a friend or a group member."""


@poke.command("private")
@click.argument("user_id")
@click.pass_context
@handle_error
def poke_private(ctx, user_id):
    """Poke USER_ID in a private chat."""
    qq_mod.poke("private", None, user_id, _backend_config(ctx))
    output({"user_id": int(user_id), "poked": True},
           message=f"Poked {user_id}", command="poke.private")


@poke.command("group")
@click.argument("group_id")
@click.argument("user_id")
@click.pass_context
@handle_error
def poke_group(ctx, group_id, user_id):
    """Poke USER_ID in GROUP_ID."""
    qq_mod.poke("group", group_id, user_id, _backend_config(ctx))
    output({"group_id": int(group_id), "user_id": int(user_id), "poked": True},
           message=f"Poked {user_id} in group {group_id}", command="poke.group")


# ── roundtrip (the closed-loop mechanism) ──────────────────────────────

@cli.command()
@click.argument("target", type=click.Choice(["private", "group"]))
@click.argument("peer_id")
@click.argument("message")
@click.pass_context
@handle_error
def roundtrip(ctx, target, peer_id, message):
    """Send a message then read it back (the full closed loop)."""
    data = qq_mod.send_then_read(target, peer_id, message, _backend_config(ctx))
    output(data, message=(
        f"Round-trip {'OK' if data['round_trip_ok'] else 'MISMATCH'} "
        f"(id={data['sent']['message_id']})"
    ), command="roundtrip")


# ── project / session (state management, local only) ───────────────────────

@cli.group()
def project():
    """Harness project state (local, no network)."""


@project.command("new")
@click.option("--name", default="qq-harness", show_default=True)
@click.option("-o", "--output", "out_path", default="qq-harness.json",
              show_default=True, help="Project filename in cwd")
@click.pass_context
@handle_error
def project_new(ctx, name, out_path):
    """Create a new project state file."""
    proj = project_mod.create_project(name)
    path = project_mod.safe_target(out_path)
    project_mod.save_project(proj, str(path))
    get_session().set_project(proj, path)
    output({"path": str(path), **project_mod.project_info(proj)},
           message=f"Created project {path}", command="project.new")


@project.command("open")
@click.argument("path")
@click.pass_context
@handle_error
def project_open(ctx, path):
    """Open an existing project state file."""
    target = project_mod.safe_target(path)
    proj = project_mod.open_project(str(target))
    get_session().set_project(proj, target)
    output(project_mod.project_info(proj), message=f"Opened {target}",
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
    """Show current project info (token is masked)."""
    output(project_mod.project_info(get_session().get_project()), command="project.info")


@cli.group()
def session():
    """Session undo / redo controls."""


@session.command("status")
@click.pass_context
def session_status(ctx):
    """Show session status."""
    output(get_session().status(), command="session.status")


@session.command("undo")
@click.pass_context
@handle_error
def session_undo(ctx):
    """Undo the last state change."""
    output(get_session().undo(), command="session.undo")


@session.command("redo")
@click.pass_context
@handle_error
def session_redo(ctx):
    """Redo the last undone change."""
    output(get_session().redo(), command="session.redo")


def main():
    cli(obj={})


if __name__ == "__main__":
    main()

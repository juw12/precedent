#!/usr/bin/env python3
"""precedent MCP server (stdio, JSON-RPC 2.0, standard library only).

Works with any MCP client: Claude Desktop / Claude Code, Cursor, Codex,
OpenCode, Gemini CLI, VS Code, Windsurf and others.

Tools:
  find_precedent   proof a task was done before (all agents' history, shell, git)
  review_reply     check a draft final answer for unproven "can't" / unverified "done"
  record_failure   count repeated failures and get loop-breaking guidance
  check_test_edit  flag edits that weaken tests
  check_command    flag destructive shortcuts
Prompt:
  precedent        the full precedent loop, usable as a slash command

Local only: no network, no telemetry. Logs go to stderr; stdout is protocol only.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import precedent_core as core  # noqa: E402

SERVER_NAME = "precedent"
SERVER_VERSION = core.VERSION
SUPPORTED_PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
SESSION = f"mcp-{os.getpid()}"

INSTRUCTIONS = (
    "precedent: fewer false 'can't's, zero fake 'done's. Call find_precedent before telling "
    "the user something is impossible, and whenever the user says you did it before. Call "
    "review_reply on your draft final answer before sending it. Call record_failure after a "
    "command or tool fails. Call check_test_edit before changing tests. Never invent a past "
    "success or output; if no precedent is found, say so and keep trying different routes."
)


def hints(title, read_only):
    """MCP tool annotations. Every tool is local only (no network) and never deletes anything."""
    return {
        "title": title,
        "readOnlyHint": read_only,
        "destructiveHint": False,
        "idempotentHint": read_only,
        "openWorldHint": False,
    }


TOOLS = [
    {
        "name": "find_precedent",
        "annotations": hints("Find precedent", True),
        "description": (
            "Search this machine for proof a task was done before: successful tool calls from past "
            "Claude Code, OpenCode, Codex and Gemini CLI sessions (with the task they served), shell history and "
            "optionally git log. Failed attempts are excluded. Use before saying you can't, or when "
            "the user says 'you did it before'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Task words, tool, file name or error text."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "cwd": {"type": "string", "description": "Current project folder, to rank its sessions first."},
                "include_git": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
    },
    {
        "name": "review_reply",
        "annotations": hints("Review draft reply", True),
        "description": (
            "Review a draft final answer before sending. Flags a give-up without evidence (and attaches "
            "precedent), a success claim without a passing check after the last change, and a cited "
            "precedent that does not exist in local history."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_reply": {"type": "string"},
                "edited_since_last_check": {
                    "type": "boolean",
                    "description": "True if you changed anything after your last test/run/read-back.",
                    "default": False,
                },
                "last_check": {
                    "type": "string",
                    "enum": ["passed", "failed", "none"],
                    "description": "Result of your most recent real check.",
                    "default": "none",
                },
                "cwd": {"type": "string"},
            },
            "required": ["draft_reply"],
        },
    },
    {
        "name": "record_failure",
        "annotations": hints("Record a failure", False),
        "description": (
            "Record a failed command or tool call. Returns how many times this same error has happened "
            "and what to do differently (switch route on the 2nd repeat, hypotheses on the 3rd)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "error_text": {"type": "string"},
                "tool": {"type": "string", "default": "tool"},
                "task": {"type": "string", "description": "What you were trying to do (improves precedent search)."},
                "cwd": {"type": "string"},
            },
            "required": ["error_text"],
        },
    },
    {
        "name": "check_test_edit",
        "annotations": hints("Check a test edit", True),
        "description": "Before editing a test file: flags skip/xfail/only markers, always-true assertions and removed assertions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["file_path", "old_text", "new_text"],
        },
    },
    {
        "name": "check_command",
        "annotations": hints("Check a shell command", True),
        "description": "Before running a shell command: flags force-push, --no-verify, reset --hard, broad recursive deletes.",
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]


def skill_text():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return INSTRUCTIONS
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip()


PROMPTS = [
    {
        "name": "precedent",
        "description": "Run the precedent loop: find proof before 'can't', run a real check before 'done'.",
        "arguments": [{"name": "task", "description": "What you are stuck on (optional).", "required": False}],
    }
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def tool_find_precedent(args):
    query = str(args.get("query") or "").strip()
    if not query:
        return "Give a query: task words, tool, file name or error text.", True
    limit = max(1, min(int(args.get("limit") or 5), 20))
    cwd = args.get("cwd") or os.getcwd()
    hits = core.find_precedents(
        query,
        cwd=cwd,
        limit=limit,
        include_git=bool(args.get("include_git", True)),
        time_budget=8.0,
    )
    if hits:
        return (
            "Possible precedent (check it matches this task before reusing; cite it if you use it):\n"
            + core.format_hits(hits)
        ), False
    return (
        f"No precedent found for: {query}. Searched {core.SOURCES_LABEL}. "
        "Do not claim to remember one; try routes that differ in kind."
    ), False


def tool_review_reply(args):
    verdict, message = core.review_reply(
        str(args.get("draft_reply") or ""),
        edited_since_last_check=bool(args.get("edited_since_last_check", False)),
        last_check=str(args.get("last_check") or "none"),
        cwd=args.get("cwd"),
    )
    return f"{verdict.upper()}: {message}", False


def tool_record_failure(args):
    error_text = str(args.get("error_text") or "")
    notes, repeats, total, signature = core.register_failure(SESSION, str(args.get("tool") or "tool"), error_text)
    lines = [f"Recorded: {signature} (this error {repeats}x, {total} failures this session)."]
    lines.extend(notes)
    if repeats >= 3 or total in (4, 8):
        hits = core.find_precedents(
            " ".join([error_text[:400], str(args.get("task") or "")]),
            cwd=args.get("cwd"),
            limit=3,
        )
        if hits:
            lines.append("Possible precedent:\n" + core.format_hits(hits))
    if len(lines) == 1:
        lines.append("Keep going, but if it happens again switch to a different kind of route.")
    return "\n".join(lines), False


def tool_check_test_edit(args):
    path = str(args.get("file_path") or "")
    if not core.is_test_path(path):
        return "OK: not a test file.", False
    issues = core.test_weakening_issues(str(args.get("old_text") or ""), str(args.get("new_text") or ""))
    if issues:
        return (
            f"STOP: this edit {', '.join(issues)}. Making a check pass by weakening it is fake success. "
            "Fix the code instead, or ask the user to approve this change explicitly."
        ), False
    return "OK: the edit does not weaken the test.", False


def tool_check_command(args):
    why = core.risky_command(str(args.get("command") or ""))
    if why:
        return f"STOP: this command {why}. Ask the user before running it.", False
    return "OK: no destructive shortcut detected.", False


HANDLERS = {
    "find_precedent": tool_find_precedent,
    "review_reply": tool_review_reply,
    "record_failure": tool_record_failure,
    "check_test_edit": tool_check_test_edit,
    "check_command": tool_check_command,
}


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------


def result(message_id, payload):
    return {"jsonrpc": "2.0", "id": message_id, "result": payload}


def error(message_id, code, text):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def handle(message):
    """Return a response dict, or None for notifications."""
    if not isinstance(message, dict):
        return error(None, -32600, "Invalid request")
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return result(message_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return result(message_id, {})
    if method == "tools/list":
        return result(message_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return error(message_id, -32602, f"Unknown tool: {name}")
        try:
            text, is_error = handler(params.get("arguments") or {})
        except Exception as exc:  # report tool errors in-band, never crash
            text, is_error = f"precedent tool error: {exc}", True
        return result(message_id, {"content": [{"type": "text", "text": text}], "isError": is_error})
    if method == "prompts/list":
        return result(message_id, {"prompts": PROMPTS})
    if method == "prompts/get":
        if params.get("name") != "precedent":
            return error(message_id, -32602, f"Unknown prompt: {params.get('name')}")
        task = str((params.get("arguments") or {}).get("task") or "").strip()
        text = skill_text() + (f"\n\nI am stuck on: {task}" if task else "")
        return result(message_id, {
            "description": PROMPTS[0]["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        })
    if is_notification:
        return None
    return error(message_id, -32601, f"Method not found: {method}")


def main():
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            response = error(None, -32700, "Parse error")
        else:
            if isinstance(message, list):
                responses = [r for r in (handle(m) for m in message) if r is not None]
                response = responses or None
            else:
                response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

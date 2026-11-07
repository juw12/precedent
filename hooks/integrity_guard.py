#!/usr/bin/env python3
"""Before-tool hook (Claude Code / Codex CLI "PreToolUse", Gemini CLI "BeforeTool"):
persistence must not turn into cheating or damage.

Stops the agent when it tries to:
- weaken a test: add skip/xfail/only, add an always-true assertion, remove
  assertions, or delete a test file;
- run a destructive shortcut: force-push, --no-verify, reset --hard, broad
  recursive deletes, deleting test files.

Claude Code asks the user to approve. Codex CLI and Gemini CLI have no "ask"
(Codex fails open on it), so there the call is denied with the reason; the
agent sees it and can ask the user, who can approve by running it themselves.
"""

import os

from _bootstrap import TOP_LEVEL_AGENTS, agent_of, core, emit, run

REPLACE_TOOLS = {"Edit", "replace", "edit", "str_replace_editor"}  # Claude/Copilot, Gemini, OpenCode
WRITE_TOOLS = {"Write", "write_file", "write", "create"}


def first(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            return value
    return ""


ASKS_USER = {"claude", "copilot"}  # agents whose tool hooks can ask the user


def stop(payload, reason):
    reason = "[precedent] " + reason
    agent = agent_of(payload)
    if agent == "gemini":
        emit({"decision": "deny", "reason": reason})
        return
    decision = "ask" if agent in ASKS_USER else "deny"
    if decision == "deny":
        reason += " If the user explicitly asked for this, tell them; they can run it themselves."
    if agent in TOP_LEVEL_AGENTS:  # Copilot CLI and the OpenCode plugin
        emit({"permissionDecision": decision, "permissionDecisionReason": reason})
        return
    emit({  # Claude Code and Codex CLI (Codex rejects any extra top-level field)
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    })


def read_existing(path, limit=1_000_000):
    try:
        if os.path.getsize(path) > limit:
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def file_edits(tool, tool_input):
    """Yield (path, old_text, new_text, deleted) for any agent's edit-style call."""
    path = first(tool_input, "file_path", "filePath", "path")
    if tool in REPLACE_TOOLS:
        yield path, first(tool_input, "old_string", "oldString", "old_str"), first(tool_input, "new_string", "newString", "new_str"), False
    elif tool == "MultiEdit":
        edits = [e for e in tool_input.get("edits") or [] if isinstance(e, dict)]
        old = "\n".join(first(e, "old_string", "oldString") for e in edits)
        new = "\n".join(first(e, "new_string", "newString") for e in edits)
        yield path, old, new, False
    elif tool in WRITE_TOOLS:
        yield path, read_existing(path), first(tool_input, "content", "file_text"), False
    else:
        for value in tool_input.values():
            if isinstance(value, str) and "*** Begin Patch" in value:
                for change in core.patch_changes(value):
                    yield change["path"], change["old"], change["new"], change["action"] == "delete"


def shell_command(tool_input):
    command = tool_input.get("command", tool_input.get("cmd", ""))
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    return str(command or "")


def handle(payload):
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return

    for path, old_text, new_text, deleted in file_edits(tool, tool_input):
        if not core.is_test_path(path):
            continue
        issues = ["deletes the test file"] if deleted else core.test_weakening_issues(old_text, new_text)
        if issues:
            stop(
                payload,
                f"This edit to {os.path.basename(path)} {', '.join(issues)}. Making a check pass by "
                "weakening it is fake success. Approve only if you asked for this; otherwise the "
                "code should be fixed, not the test.",
            )
            return

    if tool in core.SHELL_TOOLS:
        command = shell_command(tool_input)
        if "*** Begin Patch" in command:
            return
        why = core.risky_command(command)
        if why:
            stop(payload, f"This command {why}. Persistence never justifies an irreversible shortcut; approve only if intended.")


if __name__ == "__main__":
    run(handle)

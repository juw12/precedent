#!/usr/bin/env python3
"""PreToolUse hook: persistence must not turn into cheating or damage.

Asks the user first (never silently blocks) when the agent tries to:
- weaken a test: add skip/xfail/only, add an always-true assertion, or remove
  assertions;
- run a destructive shortcut: force-push, --no-verify, reset --hard, broad
  recursive deletes.
"""

import os

from _bootstrap import core, emit, run


def ask(reason):
    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "[precedent] " + reason,
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


def edit_texts(tool, tool_input):
    """Return (old_text, new_text) for an edit-style tool call."""
    if tool == "Edit":
        return str(tool_input.get("old_string", "")), str(tool_input.get("new_string", ""))
    if tool == "MultiEdit":
        edits = tool_input.get("edits") or []
        old = "\n".join(str(e.get("old_string", "")) for e in edits if isinstance(e, dict))
        new = "\n".join(str(e.get("new_string", "")) for e in edits if isinstance(e, dict))
        return old, new
    if tool == "Write":
        return read_existing(str(tool_input.get("file_path", ""))), str(tool_input.get("content", ""))
    return "", ""


def handle(payload):
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return

    if tool in ("Edit", "MultiEdit", "Write"):
        path = str(tool_input.get("file_path", ""))
        if not core.is_test_path(path):
            return
        old_text, new_text = edit_texts(tool, tool_input)
        issues = core.test_weakening_issues(old_text, new_text)
        if issues:
            ask(
                f"This edit to {os.path.basename(path)} {', '.join(issues)}. Making a check pass by "
                "weakening it is fake success. Approve only if you asked for this; otherwise the "
                "code should be fixed, not the test."
            )
        return

    if tool == "Bash":
        why = core.risky_command(str(tool_input.get("command", "")))
        if why:
            ask(f"This command {why}. Persistence never justifies an irreversible shortcut; approve only if intended.")


if __name__ == "__main__":
    run(handle)

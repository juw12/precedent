#!/usr/bin/env python3
"""See precedent work in under a minute, with no agent history of your own.

    python demo.py

Builds a throwaway sandbox (a fake past session plus the current one), runs
the real hook scripts on it exactly as Claude Code, Codex CLI and Gemini CLI
would, and prints what each agent would be told. Nothing outside a temporary
folder is read or written. Exits 1 if any scene does not behave as described,
so it doubles as a smoke test.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent / "hooks"
PANDAS = "python -c \"import pandas as pd; pd.read_excel('sales.xlsx').to_csv('sales.csv', index=False)\""


def jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return str(path)


def claude_entry(role, content, stamp):
    return {"type": role, "timestamp": stamp, "message": {"role": role, "content": content}}


def claude_tool(tool_id, name, tool_input, result, stamp, is_error=False):
    return [
        claude_entry("assistant", [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}], stamp),
        claude_entry("user", [{"type": "tool_result", "tool_use_id": tool_id, "content": result, "is_error": is_error}], stamp),
    ]


def build_sandbox(root):
    home = root / "home"
    project = home / ".claude" / "projects" / "demo"
    # A session from "last week": the CLI tool failed, pandas worked.
    jsonl(project / "last-week.jsonl", [
        claude_entry("user", "convert sales.xlsx to csv", "2026-11-01T09:00:00Z"),
        *claude_tool("t1", "Bash", {"command": "xlsx2csv sales.xlsx sales.csv"}, "bash: xlsx2csv: command not found", "2026-11-01T09:00:05Z", True),
        *claude_tool("t2", "Bash", {"command": PANDAS}, "", "2026-11-01T09:00:09Z"),
        *claude_tool("t3", "Bash", {"command": "head -3 sales.csv"}, "region,total\nnorth,10\nsouth,12", "2026-11-01T09:00:12Z"),
        claude_entry("assistant", [{"type": "text", "text": "Converted and checked sales.csv."}], "2026-11-01T09:00:15Z"),
    ])
    # Today: the agent gives up on the same kind of task.
    give_up = jsonl(project / "today-give-up.jsonl", [
        claude_entry("user", "convert the q3 report.xlsx to csv", "2026-11-07T10:00:00Z"),
        claude_entry("assistant", [{"type": "text", "text": "Sorry, I can't convert Excel files in this environment."}], "2026-11-07T10:00:04Z"),
    ])
    # Today: the agent edits code and claims success without running anything.
    unverified = jsonl(project / "today-unverified.jsonl", [
        claude_entry("user", "fix the rounding bug in totals.py", "2026-11-07T11:00:00Z"),
        *claude_tool("t9", "Edit", {"file_path": "totals.py", "old_string": "round(x)", "new_string": "round(x, 2)"}, "ok", "2026-11-07T11:00:05Z"),
        claude_entry("assistant", [{"type": "text", "text": "Done: the rounding bug is fixed and works now."}], "2026-11-07T11:00:06Z"),
    ])
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "CODEX_HOME": str(home / ".codex"),
        "PRECEDENT_CLAUDE_PROJECTS": str(home / ".claude" / "projects"),
        "PRECEDENT_GEMINI_HOME": str(home / ".gemini"),
        "PRECEDENT_OPENCODE_DB": str(home / "none.db"),
        "PRECEDENT_STATE_DIR": str(root / "state"),
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("PRECEDENT_OFF", None)
    return env, give_up, unverified


def run_hook(env, cwd, script, payload):
    result = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", env=env, cwd=cwd, timeout=60,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def message_of(out):
    if not out:
        return ""
    specific = out.get("hookSpecificOutput") or {}
    return out.get("reason") or specific.get("permissionDecisionReason") or specific.get("additionalContext") or ""


def scene(number, title, agent, out, expect, must_contain):
    text = message_of(out)
    ok = bool(out) and expect(out) and must_contain in text
    print(f"\n{number}. {title}  [{agent}]")
    print("   " + ("PASS" if ok else "UNEXPECTED") + f": {json.dumps({k: v for k, v in out.items() if k != 'reason'}) if out else 'no output'}")
    for line in text.strip().splitlines()[:8]:
        print("   | " + line)
    return ok


def main():
    root = Path(tempfile.mkdtemp(prefix="precedent-demo-"))
    try:
        env, give_up, unverified = build_sandbox(root)
        cwd = str(root)
        print("precedent demo: a sandbox with one past session; nothing outside", root, "is touched.")
        results = [
            scene(1, "Agent gives up on a task it solved last week", "Claude Code",
                  run_hook(env, cwd, "stop_guard.py", {"transcript_path": give_up, "cwd": cwd}),
                  lambda o: o.get("decision") == "block", "pd.read_excel"),
            scene(2, "Agent says 'done' after an edit, with no check", "Claude Code",
                  run_hook(env, cwd, "stop_guard.py", {"transcript_path": unverified, "cwd": cwd}),
                  lambda o: o.get("decision") == "block", "nothing ran after your last edit"),
            scene(3, "Same give-up in Gemini CLI (AfterAgent)", "Gemini CLI",
                  run_hook(env, cwd, "stop_guard.py", {
                      "hook_event_name": "AfterAgent", "cwd": cwd, "prompt": "convert the q3 report.xlsx to csv",
                      "prompt_response": "I'm unable to convert xlsx files here.", "stop_hook_active": False}),
                  lambda o: o.get("decision") == "block", "pd.read_excel"),
            scene(4, "Agent weakens a test with apply_patch", "Codex CLI",
                  run_hook(env, cwd, "integrity_guard.py", {
                      "turn_id": "t1", "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                      "tool_input": {"command": "*** Begin Patch\n*** Update File: tests/test_totals.py\n@@\n"
                                                "-    assert total([1.005]) == 1.01\n+    assert True\n*** End Patch"}}),
                  lambda o: o["hookSpecificOutput"]["permissionDecision"] == "deny", "always passes"),
        ]
        passed = sum(results)
        print(f"\n{passed}/{len(results)} scenes behaved as described.")
        print("Install for your agents: python install.py (dry run), then python install.py --apply")
        return 0 if passed == len(results) else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

"""Automatic enforcement in Codex CLI and Gemini CLI (same hook scripts, their dialects).

Payload shapes follow the agents' own sources: Codex codex-rs/hooks/src/schema.rs
(Stop has last_assistant_message + stop_hook_active; PreToolUse has no "ask",
it fails open, so we deny) and Gemini docs/hooks/reference.md (AfterAgent has
prompt_response; BeforeTool denies with decision/reason). Transcript shapes
follow the real Codex rollouts and Gemini recordings used in test_sources.
"""

import json
import re
import unittest
from pathlib import Path

from helpers import ROOT, Sandbox

import precedent_core as core

DONE = "All done: the parser is fixed and works now."
GIVE_UP = "Sorry, I can't convert Excel files in this environment."


def write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return str(path)


def codex_item(item):
    return {"timestamp": "2026-11-07T10:00:00Z", "type": "event_msg", "payload": {"type": "item_completed", "item": item}}


def codex_rollout(box, items):
    records = [
        {"timestamp": "2026-11-07T10:00:00Z", "type": "session_meta", "payload": {"cwd": "/work/app"}},
        {"timestamp": "2026-11-07T10:00:00Z", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix the parser bug"}]}},
    ] + [codex_item(item) for item in items]
    return write_jsonl(box.root / "codex" / "rollout-2026-11-07T10-00-00-cafe.jsonl", records)


PATCH = {"type": "FileChange", "status": "completed", "changes": {"/work/app/parser.py": {"type": "update"}}}
GOOD_TEST = {"type": "CommandExecution", "command": ["bash", "-lc", "pytest -q"], "status": "completed", "exit_code": 0}
BAD_TEST = {"type": "CommandExecution", "command": ["bash", "-lc", "pytest -q"], "status": "failed", "exit_code": 1}


def gemini_recording(box, calls):
    user = {"id": "u1", "type": "user", "timestamp": "2026-11-07T10:00:00Z", "content": [{"text": "fix the parser bug"}]}
    reply = {"id": "g1", "type": "gemini", "timestamp": "2026-11-07T10:00:05Z", "content": "", "toolCalls": calls}
    path = box.root / "gemini" / "chats" / "session-2026-11-07T10-00-cafe.jsonl"
    return write_jsonl(path, [{"sessionId": "s1"}, user, reply, {"$set": {"messages": [user, reply]}}])


def gemini_call(call_id, name, args, output="ok", status="success"):
    return {
        "id": call_id, "name": name, "args": args, "status": status,
        "result": [{"functionResponse": {"id": call_id, "name": name, "response": {"output": output}}}],
    }


class CodexHooks(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def stop(self, transcript, message, active=False):
        payload = {
            "session_id": "s", "turn_id": "t1", "transcript_path": transcript, "cwd": "/work/app",
            "hook_event_name": "Stop", "model": "gpt-5", "permission_mode": "default",
            "stop_hook_active": active, "last_assistant_message": message,
        }
        return self.box.run_hook("stop_guard.py", payload)

    def test_done_after_patch_without_check_is_sent_back(self):
        _, out, _ = self.stop(codex_rollout(self.box, [PATCH]), DONE)
        self.assertEqual(out["decision"], "block")
        self.assertIn("nothing ran after your last edit", out["reason"])

    def test_done_after_failing_check_is_sent_back(self):
        _, out, _ = self.stop(codex_rollout(self.box, [PATCH, BAD_TEST]), DONE)
        self.assertEqual(out["decision"], "block")
        self.assertIn("last check failed", out["reason"])

    def test_done_after_passing_check_is_accepted(self):
        _, out, _ = self.stop(codex_rollout(self.box, [PATCH, GOOD_TEST]), DONE)
        self.assertIsNone(out)

    def test_give_up_is_sent_back_once(self):
        transcript = codex_rollout(self.box, [])
        _, out, _ = self.stop(transcript, GIVE_UP)
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].strip())
        _, again, _ = self.stop(transcript, GIVE_UP, active=True)
        self.assertIsNone(again)

    def test_test_weakening_patch_is_denied_not_asked(self):
        patch = (
            "*** Begin Patch\n*** Update File: tests/test_parser.py\n@@\n"
            " def test_parse():\n-    assert parse('1') == 1\n+    assert True\n*** End Patch\n"
        )
        payload = {"session_id": "s", "turn_id": "t", "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                   "tool_input": {"command": patch}, "cwd": "/work/app"}
        _, out, _ = self.box.run_hook("integrity_guard.py", payload)
        decision = out["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny", "Codex fails open on 'ask'")
        self.assertIn("always passes", decision["permissionDecisionReason"])

    def test_deleting_a_test_file_by_patch_is_denied(self):
        patch = "*** Begin Patch\n*** Delete File: tests/test_parser.py\n*** End Patch\n"
        payload = {"turn_id": "t", "hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": patch}}
        _, out, _ = self.box.run_hook("integrity_guard.py", payload)
        self.assertIn("deletes the test file", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_force_push_is_denied(self):
        payload = {"turn_id": "t", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                   "tool_input": {"command": "git push --force origin main"}}
        _, out, _ = self.box.run_hook("integrity_guard.py", payload)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_repeated_failure_injects_context(self):
        payload = {"session_id": "codex-loop", "turn_id": "t", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_input": {"command": "npm test"}, "tool_response": {"exit_code": 1, "stderr": "Error: cannot find module x"}}
        _, first, _ = self.box.run_hook("failure_tracker.py", payload)
        _, second, _ = self.box.run_hook("failure_tracker.py", payload)
        self.assertIsNone(first)
        self.assertEqual(second["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("Same failure twice", second["hookSpecificOutput"]["additionalContext"])


class GeminiHooks(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def after_agent(self, transcript, response, active=False):
        payload = {
            "session_id": "s", "transcript_path": transcript, "cwd": "/work/app", "hook_event_name": "AfterAgent",
            "timestamp": "2026-11-07T10:00:06Z", "prompt": "fix the parser bug",
            "prompt_response": response, "stop_hook_active": active,
        }
        return self.box.run_hook("stop_guard.py", payload)

    def test_done_after_edit_without_check_is_retried(self):
        transcript = gemini_recording(self.box, [gemini_call("c1", "replace", {"file_path": "parser.py"})])
        _, out, _ = self.after_agent(transcript, DONE)
        self.assertEqual(out["decision"], "block")
        self.assertIn("nothing ran after your last edit", out["reason"])

    def test_check_with_nonzero_exit_code_counts_as_failed(self):
        transcript = gemini_recording(self.box, [
            gemini_call("c1", "replace", {"file_path": "parser.py"}),
            gemini_call("c2", "run_shell_command", {"command": "pytest -q"}, "Output: 1 failed\nExit Code: 1"),
        ])
        _, out, _ = self.after_agent(transcript, DONE)
        self.assertIn("last check failed", out["reason"])

    def test_done_after_passing_check_is_accepted(self):
        transcript = gemini_recording(self.box, [
            gemini_call("c1", "write_file", {"file_path": "parser.py", "content": "x"}),
            gemini_call("c2", "run_shell_command", {"command": "pytest -q"}, "Output: 3 passed"),
        ])
        _, out, _ = self.after_agent(transcript, DONE)
        self.assertIsNone(out)

    def test_give_up_without_transcript_still_retried(self):
        _, out, _ = self.after_agent("", GIVE_UP)
        self.assertEqual(out["decision"], "block")
        self.assertIn("Gemini CLI", out["reason"], "names every source it searched")

    def test_before_tool_denies_test_weakening_write(self):
        target = self.box.root / "tests" / "test_parser.py"
        target.parent.mkdir(parents=True)
        target.write_text("def test_parse():\n    assert parse('1') == 1\n", encoding="utf-8")
        payload = {"hook_event_name": "BeforeTool", "tool_name": "write_file",
                   "tool_input": {"file_path": str(target), "content": "import pytest\n@pytest.mark.skip\ndef test_parse():\n    pass\n"}}
        _, out, _ = self.box.run_hook("integrity_guard.py", payload)
        self.assertEqual(out["decision"], "deny")
        self.assertIn("skip", out["reason"])

    def test_before_tool_denies_risky_shell(self):
        payload = {"hook_event_name": "BeforeTool", "tool_name": "run_shell_command",
                   "tool_input": {"command": "git reset --hard HEAD~3"}}
        _, out, _ = self.box.run_hook("integrity_guard.py", payload)
        self.assertEqual(out["decision"], "deny")

    def test_after_tool_nonzero_exit_twice_injects_context(self):
        payload = {"session_id": "gem-loop", "hook_event_name": "AfterTool", "tool_name": "run_shell_command",
                   "tool_input": {"command": "make"},
                   "tool_response": {"llmContent": "Output: make: *** No rule to make target\nExit Code: 2", "returnDisplay": ""}}
        self.box.run_hook("failure_tracker.py", payload)
        _, out, _ = self.box.run_hook("failure_tracker.py", payload)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "AfterTool")
        self.assertIn("Same failure twice", out["hookSpecificOutput"]["additionalContext"])

    def test_before_agent_claim_gets_context(self):
        payload = {"hook_event_name": "BeforeAgent", "prompt": "you did this before, convert the xlsx again", "cwd": "/w"}
        _, out, _ = self.box.run_hook("prompt_trigger.py", payload)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "BeforeAgent")
        self.assertIn("Do not claim to remember", out["hookSpecificOutput"]["additionalContext"])


class ClaudeUnchanged(unittest.TestCase):
    def test_claude_still_asks(self):
        box = Sandbox()
        try:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push -f"}}
            _, out, _ = box.run_hook("integrity_guard.py", payload)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        finally:
            box.cleanup()


class Consistency(unittest.TestCase):
    def test_one_version_everywhere(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        skill = (ROOT / "skills" / "precedent" / "SKILL.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertEqual(plugin["version"], core.VERSION)
        self.assertRegex(skill, rf"version:\s*\"?{re.escape(core.VERSION)}\"?")
        first_release = re.search(r"^## (\S+)", changelog, re.MULTILINE).group(1)
        self.assertEqual(first_release.rstrip(":"), core.VERSION, "newest CHANGELOG entry")

    def test_failure_signature_never_stores_secrets(self):
        token = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"
        signature = core.failure_signature("Bash", f"Error: auth failed for key {token}")
        self.assertNotIn(token, signature)

    def test_patch_parser(self):
        changes = core.patch_changes(
            "*** Begin Patch\n*** Add File: a.py\n+x = 1\n*** Update File: b.py\n@@\n-old\n+new\n keep\n*** End Patch"
        )
        self.assertEqual([(c["action"], c["path"]) for c in changes], [("add", "a.py"), ("update", "b.py")])
        self.assertEqual(changes[1]["old"], "old\nkeep")
        self.assertEqual(changes[1]["new"], "new\nkeep")


CODEX_PRE_TOOL_KEYS = {"continue", "stopReason", "suppressOutput", "systemMessage", "decision", "reason", "hookSpecificOutput"}


class StrictOutputs(unittest.TestCase):
    """Codex rejects unknown top-level fields (serde deny_unknown_fields): never send extras."""

    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_codex_outputs_only_known_keys(self):
        deny = {"turn_id": "t", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push -f"}}
        _, out, _ = self.box.run_hook("integrity_guard.py", deny)
        self.assertTrue(set(out) <= CODEX_PRE_TOOL_KEYS, out)
        fail = {"session_id": "strict", "turn_id": "t", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_response": {"exit_code": 1, "stderr": "Error: boom"}}
        self.box.run_hook("failure_tracker.py", fail)
        _, out, _ = self.box.run_hook("failure_tracker.py", fail)
        self.assertEqual(set(out), {"hookSpecificOutput"}, out)


class CopilotHooks(unittest.TestCase):
    """Payloads in Copilot CLI's VS Code compatible shape (docs.github.com hooks reference)."""

    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def run_copilot(self, hook, payload):
        import subprocess
        import sys
        from helpers import HOOKS

        result = subprocess.run([sys.executable, str(HOOKS / hook), "--agent=copilot"], input=json.dumps(payload),
                                capture_output=True, text=True, encoding="utf-8", env=self.box.env(), timeout=60)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def test_pre_tool_use_asks_with_top_level_fields(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git reset --hard"}}
        out = self.run_copilot("integrity_guard.py", payload)
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("discards uncommitted work", out["permissionDecisionReason"])
        self.assertNotIn("hookSpecificOutput", out)

    def test_copilot_edit_argument_names(self):
        target = self.box.root / "tests" / "test_x.py"
        target.parent.mkdir(parents=True)
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                   "tool_input": {"path": str(target), "old_str": "assert f() == 2", "new_str": "assert True"}}
        out = self.run_copilot("integrity_guard.py", payload)
        self.assertIn("always passes", out["permissionDecisionReason"])

    def test_post_tool_use_exit_code_in_text(self):
        payload = {"session_id": "cp", "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "make"},
                   "tool_result": {"result_type": "success", "text_result_for_llm": "make: *** no rule\n<exited with exit code 2>"}}
        self.run_copilot("failure_tracker.py", payload)
        out = self.run_copilot("failure_tracker.py", payload)
        self.assertIn("Same failure twice", out["additionalContext"])


class OpenCodePluginPayloads(unittest.TestCase):
    """What platforms/opencode/precedent.js sends: inline client.session.messages() data."""

    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    @staticmethod
    def message(role, *parts):
        return {"info": {"role": role}, "parts": list(parts)}

    def stop(self, messages):
        import subprocess
        import sys
        from helpers import HOOKS

        payload = {"hook_event_name": "Stop", "session_id": "oc", "stop_hook_active": False, "opencode_messages": messages}
        result = subprocess.run([sys.executable, str(HOOKS / "stop_guard.py"), "--agent=opencode"], input=json.dumps(payload),
                                capture_output=True, text=True, encoding="utf-8", env=self.box.env(), timeout=60)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def tool(self, name, tool_input, status="completed", exit_code=0):
        return {"type": "tool", "tool": name, "callID": name, "state": {"status": status, "input": tool_input, "output": "",
                                                                      "metadata": {"exit": exit_code}}}

    def test_done_after_edit_without_check(self):
        out = self.stop([
            self.message("user", {"type": "text", "text": "fix the parser"}),
            self.message("assistant", self.tool("edit", {"filePath": "parser.py"}), {"type": "text", "text": DONE}),
        ])
        self.assertEqual(out["decision"], "block")

    def test_failing_bash_check_counts(self):
        out = self.stop([
            self.message("user", {"type": "text", "text": "fix the parser"}),
            self.message("assistant", self.tool("edit", {"filePath": "parser.py"}), self.tool("bash", {"command": "pytest"}, exit_code=1),
                         {"type": "text", "text": DONE}),
        ])
        self.assertIn("last check failed", out["reason"])

    def test_passing_check_accepted_and_give_up_sent_back(self):
        passing = self.stop([
            self.message("user", {"type": "text", "text": "fix the parser"}),
            self.message("assistant", self.tool("edit", {"filePath": "parser.py"}), self.tool("bash", {"command": "pytest"}),
                         {"type": "text", "text": DONE}),
        ])
        self.assertIsNone(passing)
        give_up = self.stop([
            self.message("user", {"type": "text", "text": "convert report.xlsx to csv"}),
            self.message("assistant", {"type": "text", "text": GIVE_UP}),
        ])
        self.assertEqual(give_up["decision"], "block")

    def test_opencode_tool_block_is_top_level(self):
        import subprocess
        import sys
        from helpers import HOOKS

        payload = {"hook_event_name": "PreToolUse", "tool_name": "bash", "tool_input": {"command": "git push --force"}}
        result = subprocess.run([sys.executable, str(HOOKS / "integrity_guard.py"), "--agent=opencode"], input=json.dumps(payload),
                                capture_output=True, text=True, encoding="utf-8", env=self.box.env(), timeout=60)
        out = json.loads(result.stdout)
        self.assertEqual(out["permissionDecision"], "deny")


class ShellTestRewrites(unittest.TestCase):
    def test_rewrites_flagged_and_reads_not(self):
        flagged = [
            "sed -i 's/assert x == 1/assert True/' tests/test_math.py",
            "echo 'def test_ok(): pass' > tests/test_api.py",
            "Set-Content tests/test_api.py 'x'",
        ]
        clean = ["cat tests/test_api.py", "pytest tests/test_api.py > out.log", "echo hi >> tests/notes.txt", "sed -i 's/a/b/' src/app.py"]
        for command in flagged:
            self.assertTrue(core.risky_command(command), command)
        for command in clean:
            self.assertFalse(core.risky_command(command), command)


class PermissionBypass(unittest.TestCase):
    """Found by the OpenCode A/B run: the agent removed a deny ACL to force a write."""

    def test_unlocking_commands_flagged_and_normal_ones_not(self):
        flagged = [
            'icacls "locked" /remove:g "DOMAIN\\me" /grant "DOMAIN\\me:(OI)(CI)F"',
            "icacls locked /remove:d me",
            "takeown /f locked /r",
            "Get-Acl a | Set-Acl b",
            "attrib -r report.pdf",
            "chmod u+w out/report.pdf",
            "chmod 777 locked",
        ]
        clean = ["icacls locked", "chmod +x build.sh", "chmod 755 bin/tool", "attrib +r report.pdf", "git status"]
        for command in flagged:
            self.assertTrue(core.risky_command(command), command)
        for command in clean:
            self.assertFalse(core.risky_command(command), command)


class CredentialGuessing(unittest.TestCase):
    """Found by the OpenCode A/B run: told to "keep trying", the agent sent admin:admin to a members-only page."""

    def test_default_credentials_flagged_and_real_ones_not(self):
        flagged = [
            "curl -u admin:admin http://127.0.0.1:10322/dashboard",
            "curl -H 'Authorization: Basic YWRtaW46YWRtaW4=' http://127.0.0.1/dashboard",
            "python -c \"import requests; requests.get(url, auth=('admin', 'password'))\"",
            "curl --user root:toor http://host/",
            "curl http://guest:guest@127.0.0.1/dashboard",
        ]
        clean = [
            "curl http://127.0.0.1:10322/dashboard",
            'curl -u "$API_USER:$API_TOKEN" https://api.example.com',
            "git commit -m 'admin page: fix layout'",
            "python -m pytest tests/test_admin.py",
            "pytest -k 'test_login or test_logout'",
        ]
        for command in flagged:
            self.assertTrue(core.risky_command(command), command)
        for command in clean:
            self.assertFalse(core.risky_command(command), command)


class AccessWallIsEvidence(unittest.TestCase):
    """Found by the OpenCode A/B run: sending back an honest "permission denied" report pushed the agent to strip the ACL."""

    def test_quoted_access_denial_is_not_a_bare_give_up(self):
        honest = [
            "Result: PARTIAL. Saved to work/report.pdf; locked/report.pdf failed with PermissionError: [Errno 13] Permission denied.",
            "I can't reach the dashboard: every request returns 401 Unauthorized. Please share access.",
            "I can't write there: Access is denied (Deny Write ACL on locked/).",
        ]
        for reply in honest:
            self.assertFalse(core.is_give_up(reply), reply)
        self.assertTrue(core.is_give_up("I can't do that, it's not possible."))

    def test_send_back_text_rules_out_bypassing_access_controls(self):
        verdict, message = core.review_reply("I can't do that.")
        self.assertEqual(verdict, "revise")
        self.assertIn("never means getting around an access control", message)


if __name__ == "__main__":
    unittest.main()

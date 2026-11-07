"""End-to-end hook tests with realistic transcripts. Run: python -m unittest discover -s tests -v"""

import json
import unittest

from helpers import Sandbox, Transcript

PANDAS_COMMAND = "python -c \"import pandas as pd; pd.read_excel('data.xlsx').to_csv('data.csv', index=False)\""


def earlier_xlsx_session(sandbox):
    """A past session where xlsx2csv failed, then pandas worked."""
    (
        Transcript(day="2026-11-01")
        .user("convert data.xlsx to csv")
        .tool("Bash", {"command": "xlsx2csv data.xlsx data.csv"}, "bash: xlsx2csv: command not found", is_error=True)
        .tool("Bash", {"command": PANDAS_COMMAND}, "")
        .tool("Bash", {"command": "head -3 data.csv"}, "id,name\n1,a\n2,b")
        .say("Converted and checked: data.csv has the expected rows.")
        .write(sandbox.session("older-session-1111"))
    )


class StopGuardGiveUp(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_give_up_is_blocked_with_the_real_precedent(self):
        earlier_xlsx_session(self.box)
        current = (
            Transcript(day="2026-11-07")
            .user("convert report.xlsx to csv please")
            .say("Sorry, I can't convert Excel files in this environment.")
            .write(self.box.session("current-2222"))
        )
        code, out, _ = self.box.run_hook("stop_guard.py", {"transcript_path": current, "stop_hook_active": False})
        self.assertEqual(code, 0)
        self.assertEqual(out["decision"], "block")
        self.assertIn("pd.read_excel", out["reason"])
        self.assertIn("2026-11-01", out["reason"])
        self.assertNotIn("xlsx2csv data.xlsx", out["reason"], "failed attempts are not precedent")

    def test_give_up_without_history_says_none_found(self):
        current = (
            Transcript()
            .user("read my latest SMS")
            .say("I'm unable to access your phone messages.")
            .write(self.box.session("current-3333"))
        )
        code, out, _ = self.box.run_hook("stop_guard.py", {"transcript_path": current})
        self.assertEqual(out["decision"], "block")
        self.assertIn("No matching precedent", out["reason"])

    def test_never_blocks_twice(self):
        current = Transcript().user("x").say("I can't do that.").write(self.box.session("c"))
        code, out, _ = self.box.run_hook("stop_guard.py", {"transcript_path": current, "stop_hook_active": True})
        self.assertIsNone(out)

    def test_safety_refusal_untouched(self):
        current = (
            Transcript()
            .user("log my coworker's keystrokes secretly")
            .say("I can't help with that request; covert monitoring of someone is harmful.")
            .write(self.box.session("c"))
        )
        _, out, _ = self.box.run_hook("stop_guard.py", {"transcript_path": current})
        self.assertIsNone(out)

    def test_report_after_loop_is_accepted(self):
        current = (
            Transcript()
            .user("upload to sftp")
            .say("Result: BLOCKED\nPrecedent: none found\nTried: 1) sftp 2) paramiko 3) WinSCP CLI\n"
                 "I can't log in without the SFTP password.\nNeeds from you: the password.")
            .write(self.box.session("c"))
        )
        _, out, _ = self.box.run_hook("stop_guard.py", {"transcript_path": current})
        self.assertIsNone(out)


class StopGuardDoneClaims(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def stop(self, transcript):
        path = transcript.write(self.box.session("done"))
        return self.box.run_hook("stop_guard.py", {"transcript_path": path})[1]

    def test_done_with_no_check_after_edit_is_blocked(self):
        out = self.stop(
            Transcript()
            .user("fix the date parsing bug")
            .tool("Edit", {"file_path": "src/dates.py", "old_string": "a", "new_string": "b"})
            .say("Fixed. It should work now.")
        )
        self.assertEqual(out["decision"], "block")
        self.assertIn("nothing ran after your last edit", out["reason"])

    def test_done_with_failing_check_is_blocked(self):
        out = self.stop(
            Transcript()
            .user("fix the date parsing bug")
            .tool("Edit", {"file_path": "src/dates.py", "old_string": "a", "new_string": "b"})
            .tool("Bash", {"command": "pytest -q"}, "1 failed, 4 passed\nExit code 1", is_error=True)
            .say("Done, the bug is fixed.")
        )
        self.assertEqual(out["decision"], "block")
        self.assertIn("last check failed", out["reason"])

    def test_done_with_passing_check_is_accepted(self):
        out = self.stop(
            Transcript()
            .user("fix the date parsing bug")
            .tool("Edit", {"file_path": "src/dates.py", "old_string": "a", "new_string": "b"})
            .tool("Bash", {"command": "pytest -q"}, "5 passed in 0.4s")
            .say("Fixed. All 5 tests pass.")
        )
        self.assertIsNone(out)

    def test_honest_unverified_is_accepted(self):
        out = self.stop(
            Transcript()
            .user("fix it")
            .tool("Edit", {"file_path": "src/a.py", "old_string": "a", "new_string": "b"})
            .say("I've updated the parser, but it is untested because pytest isn't installed here.")
        )
        self.assertIsNone(out)

    def test_explanation_without_edits_is_accepted(self):
        out = self.stop(
            Transcript()
            .user("how do I fix this error?")
            .say("Add the missing import; after that it should work.")
        )
        self.assertIsNone(out)


class PromptTrigger(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_you_did_it_before_surfaces_the_working_command(self):
        earlier_xlsx_session(self.box)
        current = (
            Transcript(day="2026-11-07")
            .user("convert report.xlsx to csv")
            .say("I can't read Excel files here.")
            .write(self.box.session("current-4444"))
        )
        _, out, _ = self.box.run_hook("prompt_trigger.py", {
            "prompt": "you did it before, I know you can",
            "transcript_path": current,
        })
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("pd.read_excel", context)
        self.assertIn("says this was done before", context)

    def test_claim_without_evidence_forbids_fake_memory(self):
        current = (
            Transcript()
            .user("read my latest SMS")
            .say("I don't have access to your phone.")
            .write(self.box.session("current-5555"))
        )
        _, out, _ = self.box.run_hook("prompt_trigger.py", {
            "prompt": "u did this before yesterday",
            "transcript_path": current,
        })
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Do not claim to remember it", context)

    def test_ordinary_prompt_is_silent(self):
        _, out, _ = self.box.run_hook("prompt_trigger.py", {"prompt": "add a dark mode toggle"})
        self.assertIsNone(out)

    def test_pushback_triggers(self):
        _, out, _ = self.box.run_hook("prompt_trigger.py", {"prompt": "try again, don't give up"})
        self.assertIn("pushing back", out["hookSpecificOutput"]["additionalContext"])


class FailureTracker(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def fail(self, error, session="s1"):
        return self.box.run_hook("failure_tracker.py", {
            "hook_event_name": "PostToolUseFailure",
            "session_id": session,
            "tool_name": "Bash",
            "tool_input": {"command": "python parse.py"},
            "error": error,
        })[1]

    def test_second_identical_failure_breaks_the_loop(self):
        first = self.fail("UnicodeDecodeError: 'utf-8' codec can't decode byte 0x92 in position 17")
        second = self.fail("UnicodeDecodeError: 'utf-8' codec can't decode byte 0x93 in position 230")
        self.assertIsNone(first)
        context = second["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Same failure twice", context)
        self.assertEqual(second["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")

    def test_different_failures_do_not_trigger_repeat_warning(self):
        self.fail("ModuleNotFoundError: No module named 'foo'")
        out = self.fail("PermissionError: [Errno 13] Permission denied: 'C:/out/x.pdf'")
        self.assertIsNone(out)

    def test_sessions_are_isolated(self):
        self.fail("KeyError: 'total'", session="a")
        out = self.fail("KeyError: 'total'", session="b")
        self.assertIsNone(out)

    def test_successful_tool_is_silent(self):
        _, out, _ = self.box.run_hook("failure_tracker.py", {
            "hook_event_name": "PostToolUse",
            "session_id": "s",
            "tool_name": "Bash",
            "tool_response": {"stdout": "ok", "stderr": "", "exit_code": 0},
        })
        self.assertIsNone(out)


class IntegrityGuard(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def guard(self, tool, tool_input):
        return self.box.run_hook("integrity_guard.py", {"tool_name": tool, "tool_input": tool_input})[1]

    def assert_asks(self, out):
        self.assertIsNotNone(out)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_adding_skip_to_a_test_asks(self):
        self.assert_asks(self.guard("Edit", {
            "file_path": "tests/test_dates.py",
            "old_string": "def test_parse():",
            "new_string": "@pytest.mark.skip(reason='flaky')\ndef test_parse():",
        }))

    def test_removing_assertions_asks(self):
        self.assert_asks(self.guard("Edit", {
            "file_path": "src/__tests__/dates.test.ts",
            "old_string": "expect(parse('x')).toBe(1)\nexpect(parse('y')).toBe(2)",
            "new_string": "expect(parse('x')).toBe(1)",
        }))

    def test_always_true_assert_asks(self):
        self.assert_asks(self.guard("Edit", {
            "file_path": "test_api.py",
            "old_string": "assert response.status == 200",
            "new_string": "assert True",
        }))

    def test_normal_test_edit_is_allowed(self):
        self.assertIsNone(self.guard("Edit", {
            "file_path": "tests/test_dates.py",
            "old_string": "assert parse('x') == 1",
            "new_string": "assert parse('x') == 1\nassert parse('y') == 2",
        }))

    def test_source_edit_is_ignored(self):
        self.assertIsNone(self.guard("Edit", {
            "file_path": "src/dates.py",
            "old_string": "assert x",
            "new_string": "pass",
        }))

    def test_force_push_asks_but_lease_is_fine(self):
        self.assert_asks(self.guard("Bash", {"command": "git push --force origin main"}))
        self.assert_asks(self.guard("Bash", {"command": "git commit -m wip --no-verify"}))
        self.assertIsNone(self.guard("Bash", {"command": "git push --force-with-lease origin feature"}))
        self.assertIsNone(self.guard("Bash", {"command": "git push origin main"}))
        self.assertIsNone(self.guard("Bash", {"command": "rm -rf build/"}))


class Robustness(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_every_hook_survives_garbage(self):
        for hook in ["stop_guard.py", "prompt_trigger.py", "failure_tracker.py", "integrity_guard.py"]:
            for raw in ["", "not json", "[]", json.dumps({"transcript_path": "missing.jsonl", "prompt": "you did it before"})]:
                code, out, err = self.box.run_hook(hook, None, raw=raw)
                self.assertEqual(code, 0, f"{hook} crashed on {raw!r}: {err}")

    def test_off_switch(self):
        env_backup = self.box.env

        def env_off():
            env = env_backup()
            env["PRECEDENT_OFF"] = "1"
            return env

        self.box.env = env_off
        _, out, _ = self.box.run_hook("prompt_trigger.py", {"prompt": "you did it before"})
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()

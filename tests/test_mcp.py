"""MCP server: real stdio JSON-RPC session, as an MCP client would run it."""

import json
import subprocess
import sys
import unittest

from helpers import SCRIPTS, OpenCodeDB, Sandbox


class McpSession:
    def __init__(self, env, server=None):
        self.proc = subprocess.Popen(
            [sys.executable, str(server or SCRIPTS / "mcp_server.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        if notify:
            return None
        line = self.proc.stdout.readline()
        return json.loads(line.decode("utf-8"))

    def call(self, tool, arguments):
        response = self.send("tools/call", {"name": tool, "arguments": arguments})
        return response["result"]["content"][0]["text"], response["result"]["isError"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()


class McpProtocol(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        db = OpenCodeDB(self.box.home / ".local" / "share" / "opencode" / "opencode.db")
        sid = db.session("/work/demo")
        db.user(sid, "convert data.xlsx to csv")
        db.tool(sid, "bash", {"command": "python convert_with_pandas.py data.xlsx"})
        db.close()
        self.mcp = McpSession(self.box.env())

    def tearDown(self):
        self.mcp.close()
        self.box.cleanup()

    def test_handshake_and_listing(self):
        init = self.mcp.send("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        })
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(init["result"]["serverInfo"]["name"], "precedent")
        self.assertIn("find_precedent", init["result"]["instructions"])
        self.assertIsNone(self.mcp.send("notifications/initialized", notify=True))
        tools = {t["name"] for t in self.mcp.send("tools/list")["result"]["tools"]}
        self.assertEqual(tools, {"find_precedent", "review_reply", "record_failure", "check_test_edit", "check_command"})
        prompts = self.mcp.send("prompts/list")["result"]["prompts"]
        self.assertEqual(prompts[0]["name"], "precedent")
        prompt = self.mcp.send("prompts/get", {"name": "precedent", "arguments": {"task": "sftp upload"}})
        text = prompt["result"]["messages"][0]["content"]["text"]
        self.assertIn("route ladder", text.lower())
        self.assertIn("sftp upload", text)

    def test_every_tool_declares_all_four_hints(self):
        tools = self.mcp.send("tools/list")["result"]["tools"]
        hint_names = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
        for tool in tools:
            annotations = tool.get("annotations") or {}
            for hint in hint_names:
                self.assertIsInstance(annotations.get(hint), bool, f"{tool['name']} lacks {hint}")
            self.assertFalse(annotations["destructiveHint"], tool["name"])
            self.assertFalse(annotations["openWorldHint"], tool["name"])
        read_only = {t["name"] for t in tools if t["annotations"]["readOnlyHint"]}
        self.assertEqual(read_only, {"find_precedent", "review_reply", "check_test_edit", "check_command"})

    def test_old_protocol_version_is_negotiated(self):
        init = self.mcp.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")

    def test_find_precedent_reads_other_agents(self):
        text, is_error = self.mcp.call("find_precedent", {"query": "convert report.xlsx to csv"})
        self.assertFalse(is_error)
        self.assertIn("convert_with_pandas.py", text)
        self.assertIn("OpenCode", text)

    def test_find_precedent_none_found(self):
        text, _ = self.mcp.call("find_precedent", {"query": "read my latest sms messages"})
        self.assertIn("No precedent found", text)
        self.assertIn("Do not claim to remember", text)

    def test_review_reply(self):
        give_up, _ = self.mcp.call("review_reply", {"draft_reply": "I can't convert Excel files."})
        self.assertTrue(give_up.startswith("REVISE"))
        unverified, _ = self.mcp.call("review_reply", {"draft_reply": "Fixed, it should work now.", "edited_since_last_check": True})
        self.assertTrue(unverified.startswith("REVISE"))
        failed, _ = self.mcp.call("review_reply", {"draft_reply": "Done.", "last_check": "failed"})
        self.assertIn("last check failed", failed)
        good, _ = self.mcp.call("review_reply", {"draft_reply": "Fixed: all 12 tests pass.", "last_check": "passed"})
        self.assertTrue(good.startswith("OK"))

    def test_record_failure_breaks_loops(self):
        first, _ = self.mcp.call("record_failure", {"error_text": "ConnectionRefusedError: [Errno 111] port 5432", "tool": "bash"})
        second, _ = self.mcp.call("record_failure", {"error_text": "ConnectionRefusedError: [Errno 111] port 5433", "tool": "bash"})
        self.assertIn("1x", first)
        self.assertIn("Same failure twice", second)

    def test_guards(self):
        test_edit, _ = self.mcp.call("check_test_edit", {
            "file_path": "tests/test_api.py", "old_text": "assert r.ok", "new_text": "assert True"})
        self.assertTrue(test_edit.startswith("STOP"))
        command, _ = self.mcp.call("check_command", {"command": "git push -f origin main"})
        self.assertTrue(command.startswith("STOP"))
        safe, _ = self.mcp.call("check_command", {"command": "git status"})
        self.assertTrue(safe.startswith("OK"))

    def test_errors_do_not_kill_the_server(self):
        unknown = self.mcp.send("tools/call", {"name": "nope", "arguments": {}})
        self.assertEqual(unknown["error"]["code"], -32602)
        missing = self.mcp.send("does/not/exist")
        self.assertEqual(missing["error"]["code"], -32601)
        self.mcp.proc.stdin.write(b"this is not json\n")
        self.mcp.proc.stdin.flush()
        parse = json.loads(self.mcp.proc.stdout.readline())
        self.assertEqual(parse["error"]["code"], -32700)
        self.assertEqual(self.mcp.send("ping")["result"], {})


if __name__ == "__main__":
    unittest.main()

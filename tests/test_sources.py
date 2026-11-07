"""Cross-agent history: OpenCode (real schema) and Codex (documented rollout format)."""

import json
import os
import unittest

from helpers import OpenCodeDB, Sandbox

import precedent_core as core


class EnvSandbox(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.box.env())

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        self.box.cleanup()


class OpenCodeHistory(EnvSandbox):
    def build(self):
        db = OpenCodeDB(self.box.home / ".local" / "share" / "opencode" / "opencode.db")
        sid = db.session("C:\\work\\demo")
        db.user(sid, "convert data.xlsx to csv")
        db.tool(sid, "bash", {"command": "xlsx2csv data.xlsx"}, exit_code=127, output="command not found")
        db.tool(sid, "bash", {"command": "python -c \"import pandas; pandas.read_excel('data.xlsx').to_csv('data.csv')\""})
        db.tool(sid, "read", {"filePath": "data.csv"})
        db.tool(sid, "webfetch", {"url": "https://example.com/broken"}, status="error")
        db.close()

    def test_successful_tool_call_is_found_with_task_and_agent(self):
        self.build()
        hits = core.find_precedents("convert report.xlsx to csv", cwd="C:\\work\\demo")
        self.assertTrue(hits)
        top = hits[0]
        self.assertEqual(top["agent"], "OpenCode")
        self.assertIn("read_excel", top["summary"])
        self.assertTrue(top["same_project"])
        self.assertIn("convert data.xlsx", top["task"])

    def test_nonzero_exit_and_error_status_are_not_precedent(self):
        self.build()
        hits = core.find_precedents("xlsx2csv convert csv broken example", cwd="C:\\work\\demo", limit=10)
        summaries = " ".join(h["summary"] for h in hits)
        self.assertNotIn("xlsx2csv data.xlsx", summaries)
        self.assertNotIn("example.com/broken", summaries)

    def test_lookup_tools_are_skipped(self):
        self.build()
        hits = core.find_precedents("data.csv read", limit=10)
        self.assertFalse(any(h["tool"] == "read" for h in hits))

    def test_formatting_names_the_agent(self):
        self.build()
        text = core.format_hits(core.find_precedents("convert xlsx csv", cwd="C:\\work\\demo"))
        self.assertIn("OpenCode, this project", text)

    def test_missing_database_is_harmless(self):
        self.assertEqual(core.find_precedents("anything at all here"), [])


class CodexHistory(EnvSandbox):
    def write_rollout(self, records):
        path = self.box.home / ".codex" / "sessions" / "2026" / "11" / "02" / "rollout-2026-11-02T10-00-00-abcd1234.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def test_function_calls_with_exit_codes(self):
        stamp = "2026-11-02T10:00:00Z"
        self.write_rollout([
            {"timestamp": stamp, "type": "session_meta", "payload": {"cwd": "/work/site"}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "deploy the docs site"}]}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call", "name": "shell", "call_id": "c1",
                "arguments": json.dumps({"command": ["bash", "-lc", "mkdocs gh-deploy"]})}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "c1",
                "output": json.dumps({"output": "error: not a git repo", "metadata": {"exit_code": 1}})}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call", "name": "shell", "call_id": "c2",
                "arguments": json.dumps({"command": ["bash", "-lc", "cd site && mkdocs gh-deploy --force"]})}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "c2",
                "output": json.dumps({"output": "INFO deployed", "metadata": {"exit_code": 0}})}},
        ])
        hits = core.find_precedents("please deploy the docs again", cwd="/work/site")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["agent"], "Codex")
        self.assertEqual(hits[0]["summary"], "cd site && mkdocs gh-deploy --force")
        self.assertTrue(hits[0]["same_project"])
        self.assertFalse(any(h["summary"] == "mkdocs gh-deploy" for h in hits))

    def test_plain_text_exit_code_output(self):
        stamp = "2026-11-02T10:00:00Z"
        self.write_rollout([
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "resize the images"}]}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call", "name": "shell", "call_id": "x",
                "arguments": json.dumps({"command": "magick mogrify -resize 800 img/*.png"})}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "x", "output": "Exit code: 0\nWall time: 2s"}},
        ])
        hits = core.find_precedents("resize images to 800")
        self.assertTrue(any("mogrify" in h["summary"] for h in hits))


if __name__ == "__main__":
    unittest.main()

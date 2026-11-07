"""Installer: dry run writes nothing, apply is correct and idempotent, uninstall is clean."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import ROOT, Sandbox
from test_mcp import McpSession


def snapshot(folder):
    return {
        str(p.relative_to(folder)): p.read_bytes()
        for p in sorted(Path(folder).rglob("*"))
        if p.is_file()
    }


class Installer(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        h = self.box.home
        (h / ".claude").mkdir(exist_ok=True)
        (h / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark", "hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "echo mine"}]}]}}), encoding="utf-8")
        (h / ".codex").mkdir()
        (h / ".codex" / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
        (h / ".config" / "opencode").mkdir(parents=True)
        (h / ".config" / "opencode" / "opencode.json").write_text(
            json.dumps({"$schema": "https://opencode.ai/config.json", "mcp": {"other": {"type": "remote"}}}), encoding="utf-8")
        (h / ".config" / "opencode" / "AGENTS.md").write_text("# My rules\n\nBe nice.\n", encoding="utf-8")
        (h / ".gemini").mkdir()
        (h / ".gemini" / "settings.json").write_text(json.dumps({"theme": "Dracula"}), encoding="utf-8")
        (h / ".cursor").mkdir()
        (h / "AppData" / "Code" / "User").mkdir(parents=True)
        (h / "AppData" / "Code" / "User" / "mcp.json").write_text('{\n  // my servers\n  "servers": {}\n}\n', encoding="utf-8")

    def tearDown(self):
        self.box.cleanup()

    def run_installer(self, *args):
        result = subprocess.run(
            [sys.executable, str(ROOT / "install.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.box.env(),
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def read_json(self, *parts):
        return json.loads(self.box.home.joinpath(*parts).read_text(encoding="utf-8"))

    def test_dry_run_changes_nothing(self):
        before = snapshot(self.box.home)
        out = self.run_installer()
        self.assertIn("dry run", out)
        self.assertIn("[Codex CLI]", out)
        self.assertIn("[OpenCode]", out)
        self.assertEqual(snapshot(self.box.home), before)

    def test_apply_installs_everywhere_and_preserves_user_config(self):
        out = self.run_installer("--apply")
        h = self.box.home
        server = str(h / ".precedent" / "skills" / "precedent" / "scripts" / "mcp_server.py")

        self.assertTrue((h / ".precedent" / "skills" / "precedent" / "scripts" / "mcp_server.py").is_file())
        for skills in [".claude/skills", ".codex/skills", ".config/opencode/skills", ".gemini/skills"]:
            self.assertTrue((h / skills / "precedent" / "SKILL.md").is_file(), skills)

        opencode = self.read_json(".config", "opencode", "opencode.json")
        self.assertEqual(opencode["mcp"]["other"], {"type": "remote"})
        self.assertEqual(opencode["mcp"]["precedent"]["command"][1], server)
        self.assertEqual(self.read_json(".gemini", "settings.json")["theme"], "Dracula")
        self.assertEqual(self.read_json(".gemini", "settings.json")["mcpServers"]["precedent"]["args"], [server])
        self.assertIn("precedent", self.read_json(".cursor", "mcp.json")["mcpServers"])
        self.assertEqual(self.read_json(".claude.json")["mcpServers"]["precedent"]["type"], "stdio")

        claude = self.read_json(".claude", "settings.json")
        self.assertEqual(claude["theme"], "dark")
        stop_commands = [h2["command"] for g in claude["hooks"]["Stop"] for h2 in g["hooks"]]
        self.assertIn("echo mine", stop_commands)
        self.assertTrue(any("stop_guard.py" in c for c in stop_commands))

        toml = (h / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5"', toml)
        self.assertIn("[mcp_servers.precedent]", toml)
        agents_md = (h / ".config" / "opencode" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents_md.startswith("# My rules"))
        self.assertIn("<!-- precedent:start -->", agents_md)
        self.assertTrue((h / ".codex" / "AGENTS.md").is_file())
        self.assertTrue((h / ".gemini" / "GEMINI.md").is_file())

        self.assertIn("SKIP", out)
        self.assertIn("// my servers", (h / "AppData" / "Code" / "User" / "mcp.json").read_text(encoding="utf-8"))
        self.assertTrue((h / ".config" / "opencode" / "opencode.json.precedent-bak").is_file())

    def test_second_apply_is_idempotent(self):
        self.run_installer("--apply")
        first = snapshot(self.box.home)
        self.run_installer("--apply")
        second = snapshot(self.box.home)
        changed = [k for k in first if first[k] != second.get(k)]
        self.assertEqual(changed, [], f"files changed on re-run: {changed}")
        agents_md = (self.box.home / ".config" / "opencode" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agents_md.count("<!-- precedent:start -->"), 1)

    def test_installed_mcp_server_runs(self):
        self.run_installer("--apply")
        server = self.box.home / ".precedent" / "skills" / "precedent" / "scripts" / "mcp_server.py"
        session = McpSession(self.box.env(), server=server)
        try:
            init = session.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
            self.assertEqual(init["result"]["serverInfo"]["name"], "precedent")
            text, _ = session.call("check_command", {"command": "git reset --hard"})
            self.assertTrue(text.startswith("STOP"))
        finally:
            session.close()

    def test_uninstall_removes_only_precedent(self):
        self.run_installer("--apply")
        self.run_installer("--uninstall", "--apply")
        h = self.box.home
        self.assertFalse((h / ".precedent").exists())
        for skills in [".claude/skills", ".codex/skills", ".config/opencode/skills", ".gemini/skills"]:
            self.assertFalse((h / skills / "precedent").exists(), skills)
        opencode = self.read_json(".config", "opencode", "opencode.json")
        self.assertEqual(opencode["mcp"], {"other": {"type": "remote"}})
        self.assertNotIn("precedent", self.read_json(".gemini", "settings.json").get("mcpServers", {}))
        claude = self.read_json(".claude", "settings.json")
        self.assertEqual([h2["command"] for g in claude["hooks"]["Stop"] for h2 in g["hooks"]], ["echo mine"])
        self.assertNotIn("UserPromptSubmit", claude["hooks"])
        toml = (h / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(toml.strip(), 'model = "gpt-5"')
        self.assertEqual((h / ".config" / "opencode" / "AGENTS.md").read_text(encoding="utf-8").strip(), "# My rules\n\nBe nice.")

    def test_agent_filter(self):
        out = self.run_installer("--agents", "cursor")
        self.assertIn("[Cursor]", out)
        self.assertNotIn("[OpenCode]", out)


if __name__ == "__main__":
    unittest.main()

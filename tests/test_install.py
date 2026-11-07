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


class AgentHooks(unittest.TestCase):
    """Codex CLI hooks.json and Gemini CLI settings.json hooks: written, runnable, removable."""

    def setUp(self):
        self.box = Sandbox()
        h = self.box.home
        (h / ".codex").mkdir()
        (h / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "echo mine"}]}]}}), encoding="utf-8")
        (h / ".gemini").mkdir()
        (h / ".gemini" / "settings.json").write_text(json.dumps({"theme": "Dracula"}), encoding="utf-8")

    def tearDown(self):
        self.box.cleanup()

    def install(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), *args], capture_output=True,
                                text=True, encoding="utf-8", env=self.box.env(), timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def config(self, *parts):
        return json.loads(self.box.home.joinpath(*parts).read_text(encoding="utf-8"))

    @staticmethod
    def commands(hooks, event):
        return [h["command"] for group in hooks.get(event, []) for h in group["hooks"]]

    def test_codex_and_gemini_hooks_installed_idempotent_and_removed(self):
        out = self.install("--apply", "--agents", "codex,gemini")
        self.assertIn("/hooks", out, "tells the user to trust hooks in Codex")
        codex = self.config(".codex", "hooks.json")["hooks"]
        self.assertEqual(set(codex), {"Stop", "UserPromptSubmit", "PreToolUse", "PostToolUse"})
        self.assertIn("echo mine", self.commands(codex, "Stop"))
        self.assertTrue(any(c.endswith("stop_guard.py --agent=codex") for c in self.commands(codex, "Stop")))
        gemini = self.config(".gemini", "settings.json")
        self.assertEqual(gemini["theme"], "Dracula")
        after_agent = gemini["hooks"]["AfterAgent"][0]["hooks"][0]
        self.assertEqual(after_agent["timeout"], 15000, "Gemini timeouts are milliseconds")
        self.assertEqual(after_agent["name"], "precedent-stop-guard")
        self.assertEqual(gemini["hooks"]["BeforeTool"][0]["matcher"], "run_shell_command|write_file|replace")

        first = (self.box.home / ".codex" / "hooks.json").read_bytes(), (self.box.home / ".gemini" / "settings.json").read_bytes()
        self.install("--apply", "--agents", "codex,gemini")
        second = (self.box.home / ".codex" / "hooks.json").read_bytes(), (self.box.home / ".gemini" / "settings.json").read_bytes()
        self.assertEqual(first, second, "re-running changes nothing")

        self.install("--uninstall", "--apply", "--agents", "codex,gemini")
        self.assertEqual(self.config(".codex", "hooks.json")["hooks"], {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]})
        self.assertNotIn("hooks", self.config(".gemini", "settings.json"))

    def test_installed_commands_run_in_every_available_shell(self):
        import shutil

        self.install("--apply", "--agents", "codex,gemini")
        payload = json.dumps({"turn_id": "t", "hook_event_name": "Stop", "stop_hook_active": False,
                              "last_assistant_message": "Sorry, I can't do that in this environment."})
        command = next(c for c in self.commands(self.config(".codex", "hooks.json")["hooks"], "Stop") if "stop_guard" in c)
        shells = [("default", None)]
        for name, argv in (("bash", ["bash", "-c"]), ("powershell", ["powershell", "-NoProfile", "-Command"]),
                           ("cmd", ["cmd", "/d", "/s", "/c"])):
            if shutil.which(argv[0]):
                shells.append((name, argv))
        ran = []
        for name, argv in shells:
            if argv is None:
                result = subprocess.run(command, shell=True, input=payload, capture_output=True, text=True,
                                        encoding="utf-8", env=self.box.env(), timeout=60)
            else:
                result = subprocess.run(argv + [command], input=payload, capture_output=True, text=True,
                                        encoding="utf-8", env=self.box.env(), timeout=60)
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")
            self.assertEqual(json.loads(result.stdout)["decision"], "block", f"{name}: {result.stdout!r}")
            ran.append(name)
        self.assertGreaterEqual(len(ran), 2, ran)


class Demo(unittest.TestCase):
    def test_demo_runs_and_every_scene_passes(self):
        result = subprocess.run([sys.executable, str(ROOT / "demo.py")], capture_output=True, text=True,
                                encoding="utf-8", timeout=120, env=dict(Sandbox().env()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("4/4 scenes behaved as described", result.stdout)


class ForeignSkills(unittest.TestCase):
    """Another tool's skill with the same folder name is never overwritten or deleted."""

    def setUp(self):
        self.box = Sandbox()
        h = self.box.home
        (h / ".config" / "opencode").mkdir(parents=True)
        (h / ".config" / "opencode" / "opencode.json").write_text("{}", encoding="utf-8")
        self.foreign = h / ".config" / "opencode" / "skills" / "precedent"
        self.foreign.mkdir(parents=True)
        (self.foreign / "SKILL.md").write_text("---\nname: precedent\ndescription: someone else's autopilot\n---\n", encoding="utf-8")
        (self.foreign / "profile.md").write_text("mine", encoding="utf-8")

    def tearDown(self):
        self.box.cleanup()

    def install(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), *args], capture_output=True, text=True,
                                encoding="utf-8", env=self.box.env(), timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_install_and_uninstall_leave_foreign_skill_alone(self):
        before = snapshot(self.foreign)
        out = self.install("--apply", "--agents", "opencode")
        self.assertIn("another skill already uses this folder", out)
        self.assertEqual(snapshot(self.foreign), before)
        self.install("--uninstall", "--apply", "--agents", "opencode")
        self.assertEqual(snapshot(self.foreign), before)

    def test_folder_alone_is_not_an_agent(self):
        h = self.box.home
        (h / ".gemini" / "skills" / "precedent").mkdir(parents=True)
        env = self.box.env()
        env["PATH"] = ""
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--list"], capture_output=True, text=True,
                                encoding="utf-8", env=env, timeout=60)
        gemini_line = next(line for line in result.stdout.splitlines() if " gemini " in line)
        self.assertTrue(gemini_line.startswith("-"), gemini_line)


class OpenCodePlugin(unittest.TestCase):
    """OpenCode gets an auto-loaded plugin folder whose shim points at the payload copy."""

    def setUp(self):
        self.box = Sandbox()
        h = self.box.home
        (h / ".config" / "opencode").mkdir(parents=True)
        (h / ".config" / "opencode" / "opencode.json").write_text("{}", encoding="utf-8")
        self.plugin_dir = h / ".config" / "opencode" / "plugins" / "precedent"

    def tearDown(self):
        self.box.cleanup()

    def install(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), *args], capture_output=True, text=True,
                                encoding="utf-8", env=self.box.env(), timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_plugin_installed_points_at_payload_and_is_removed(self):
        self.install("--apply", "--agents", "opencode")
        package = json.loads((self.plugin_dir / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["exports"]["."], "./index.js")
        shim = (self.plugin_dir / "index.js").read_text(encoding="utf-8")
        payload_js = self.box.home / ".precedent" / "platforms" / "opencode" / "precedent.js"
        self.assertTrue(payload_js.is_file())
        self.assertIn(payload_js.as_uri(), shim)
        config = json.loads(payload_js.with_name("precedent.config.json").read_text(encoding="utf-8"))
        self.assertTrue(Path(config["hooks"], "integrity_guard.py").is_file())
        self.assertTrue(Path(config["rules"]).is_file())
        self.install("--uninstall", "--apply", "--agents", "opencode")
        self.assertFalse(self.plugin_dir.exists())

    def test_plugin_is_a_v2_default_export_with_classic_fallback(self):
        source = (ROOT / "platforms" / "opencode" / "precedent.js").read_text(encoding="utf-8")
        self.assertIn('export default { id: "precedent", setup, server: PrecedentPlugin }', source)
        named_exports = [line for line in source.splitlines() if line.startswith("export ") and "default" not in line]
        self.assertEqual(named_exports, [], "classic loaders call every named export as a plugin")
        for hook in ('ctx.tool.hook("execute.before"', 'ctx.tool.hook("execute.after"',
                     'ctx.session.hook("context"', 'ctx.session.hook("prompt"', "session.execution.succeeded"):
            self.assertIn(hook, source)


if __name__ == "__main__":
    unittest.main()

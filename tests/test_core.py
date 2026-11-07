"""Unit tests for the precedent engine and repo packaging."""

import json
import re
import time
import unittest
from pathlib import Path

from helpers import HOOKS, ROOT, Sandbox, Transcript

import precedent_core as core


class Signals(unittest.TestCase):
    def test_give_up_variants(self):
        for text in [
            "I can't do that.",
            "I can’t run that.",
            "I'm unable to open the app.",
            "I’m not able to reach the API.",
            "That's not possible with this tool.",
            "You'll need to do this manually in Excel.",
            "There is no way for me to access it.",
        ]:
            self.assertTrue(core.is_give_up(text), text)

    def test_not_give_up(self):
        for text in [
            "Done. The PDF is saved and I re-opened it.",
            "I can do that now.",
            "I can't help with that request; it would be harmful.",
        ]:
            self.assertFalse(core.is_give_up(text), text)

    def test_precedent_claims(self):
        for text in [
            "you did it before",
            "u did this before",
            "You have done this many times",
            "it worked yesterday",
            "last time you used a script",
            "you already did this last week",
        ]:
            self.assertTrue(core.matches_any(core.PRECEDENT_CLAIM, text), text)
        self.assertFalse(core.matches_any(core.PRECEDENT_CLAIM, "do it before lunch"))

    def test_done_claims(self):
        self.assertTrue(core.is_done_claim("Fixed. It should work now."))
        self.assertTrue(core.is_done_claim("I've updated the config."))
        self.assertFalse(core.is_done_claim("I've updated it but it's untested."))
        self.assertFalse(core.is_done_claim("Here is how the parser works."))


class Redaction(unittest.TestCase):
    def test_secrets_are_removed(self):
        # Built at runtime so secret scanners don't flag the fixture itself.
        github_token = "ghp" + "_" + "abcdefghijklmnop"
        openai_key = "sk" + "-" + "abcdefghijklmnop"
        text = (
            "curl -H 'Authorization: Bearer abc.def-123' https://user:hunter2@example.com "
            f"--password=s3cret token={github_token} API_KEY: {openai_key}"
        )
        cleaned = core.redact(text)
        for secret in ["abc.def-123", "hunter2", "s3cret", github_token, openai_key]:
            self.assertNotIn(secret, cleaned)

    def test_ordinary_flags_survive(self):
        self.assertEqual(core.redact("Get-ChildItem -Path C:\\data -Recurse"), "Get-ChildItem -Path C:\\data -Recurse")
        self.assertEqual(core.redact("mkdir -p build/out"), "mkdir -p build/out")


class Search(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.old_env = dict(__import__("os").environ)
        __import__("os").environ.update(self.box.env())

    def tearDown(self):
        import os
        os.environ.clear()
        os.environ.update(self.old_env)
        self.box.cleanup()

    def test_task_words_find_command_that_shares_no_words(self):
        (
            Transcript(day="2026-10-30")
            .user("deploy the docs site")
            .tool("Bash", {"command": "mkdocs gh-deploy --force"}, "INFO - Your documentation should shortly be available")
            .write(self.box.session("docs-session"))
        )
        hits = core.find_precedents("please deploy docs again")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["summary"], "mkdocs gh-deploy --force")

    def test_current_failed_turn_is_not_precedent(self):
        current = (
            Transcript()
            .user("convert data.xlsx to csv")
            .tool("Bash", {"command": "python convert_xlsx.py"}, "")
            .write(self.box.session("current"))
        )
        hits = core.find_precedents("convert xlsx csv", current_path=current)
        self.assertEqual(hits, [])

    def test_shell_history_is_searched_and_labelled(self):
        history = self.box.home / ".bash_history"
        history.write_text("ls\nffmpeg -i in.mov -vf scale=800:-1 out.mp4\n", encoding="utf-8")
        hits = core.find_precedents("resize video with ffmpeg")
        self.assertTrue(any("ffmpeg" in h["summary"] and h["outcome"] == "outcome unknown" for h in hits))

    def test_search_is_fast_on_large_history(self):
        for index in range(25):
            transcript = Transcript()
            for turn in range(40):
                transcript.user(f"task {turn} about widgets and gadgets {index}")
                for step in range(10):
                    transcript.tool("Bash", {"command": f"python tool_{step}.py --widget {turn}"}, "ok")
            transcript.write(self.box.session(f"bulk-{index}"))
        start = time.monotonic()
        core.find_precedents("widget tool gadgets")
        self.assertLess(time.monotonic() - start, 6.0)


class Packaging(unittest.TestCase):
    def test_skill_frontmatter(self):
        skill = ROOT / "skills" / "precedent" / "SKILL.md"
        front = skill.read_text(encoding="utf-8").split("---")[1]
        name = re.search(r"^name:\s*(.+)$", front, re.M).group(1).strip()
        description = re.search(r"^description:\s*(.+)$", front, re.M).group(1).strip()
        self.assertEqual(name, skill.parent.name)
        self.assertRegex(name, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertLessEqual(len(description), 1024)

    def test_hooks_json_points_at_real_scripts(self):
        config = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
        scripts = set()
        for groups in config["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    scripts.update(re.findall(r"hooks/([a-z_]+\.py)", hook["command"]))
        self.assertEqual(scripts, {"prompt_trigger.py", "integrity_guard.py", "failure_tracker.py", "stop_guard.py"})
        for script in scripts:
            self.assertTrue((HOOKS / script).is_file(), script)

    def test_plugin_manifests_are_valid(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "precedent")
        self.assertEqual(market["plugins"][0]["name"], "precedent")

    def test_versions_agree(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        skill = (ROOT / "skills" / "precedent" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(f'version: "{plugin["version"]}"', skill)


if __name__ == "__main__":
    unittest.main()

"""Build realistic Claude Code transcripts and run hooks in an isolated home."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
SCRIPTS = ROOT / "skills" / "precedent" / "scripts"

sys.path.insert(0, str(SCRIPTS))


class Transcript:
    """Accumulates JSONL entries in Claude Code's transcript shape."""

    def __init__(self, day="2026-11-05"):
        self.entries = []
        self.day = day
        self.counter = 0

    def _stamp(self):
        self.counter += 1
        return f"{self.day}T10:{self.counter // 60:02d}:{self.counter % 60:02d}.000Z"

    def user(self, text):
        self.entries.append({
            "type": "user",
            "timestamp": self._stamp(),
            "message": {"role": "user", "content": text},
        })
        return self

    def say(self, text):
        self.entries.append({
            "type": "assistant",
            "timestamp": self._stamp(),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        })
        return self

    def tool(self, name, tool_input, result="ok", is_error=False):
        self.counter += 1
        tool_id = f"toolu_{self.counter:04d}"
        self.entries.append({
            "type": "assistant",
            "timestamp": self._stamp(),
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
            },
        })
        self.entries.append({
            "type": "user",
            "timestamp": self._stamp(),
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result,
                    "is_error": is_error,
                }],
            },
        })
        return self

    def write(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for entry in self.entries:
                handle.write(json.dumps(entry) + "\n")
        return str(path)


class Sandbox:
    """Temporary home with its own ~/.claude/projects, history and state dir."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="precedent-test-"))
        self.home = self.root / "home"
        self.projects = self.home / ".claude" / "projects"
        self.project = self.projects / "C--work-demo"
        self.project.mkdir(parents=True)
        (self.home / "AppData").mkdir()

    def env(self):
        env = dict(os.environ)
        env.update({
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "APPDATA": str(self.home / "AppData"),
            "PRECEDENT_CLAUDE_PROJECTS": str(self.projects),
            "PRECEDENT_STATE_DIR": str(self.root / "state"),
            "PYTHONIOENCODING": "utf-8",
        })
        env.pop("PRECEDENT_OFF", None)
        return env

    def session(self, name):
        return self.project / f"{name}.jsonl"

    def run_hook(self, hook, payload, raw=None):
        result = subprocess.run(
            [sys.executable, str(HOOKS / hook)],
            input=raw if raw is not None else json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env(),
            timeout=60,
        )
        output = result.stdout.strip()
        return result.returncode, (json.loads(output) if output else None), result.stderr

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

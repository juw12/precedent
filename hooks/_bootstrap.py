"""Shared setup for precedent hooks: import the core and run safely.

Every hook exits 0 no matter what, so a bug here can never stop the agent or
block the user. Set PRECEDENT_OFF=1 to disable all hooks.

The same scripts serve Claude Code, Codex CLI and Gemini CLI. The agent is
taken from an --agent=<id> argument (the installer adds it) or inferred from
the payload, and replies are written in that agent's dialect.
"""

import json
import os
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HOOKS_DIR, "..", "skills", "precedent", "scripts"))

import precedent_core as core  # noqa: E402,F401


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")


def agent_of(payload):
    return core.detect_agent(payload, sys.argv[1:])


def add_context(payload, event_default, text):
    """Inject text for the model; same shape works for all three agents."""
    emit({
        "hookSpecificOutput": {
            "hookEventName": str(payload.get("hook_event_name") or event_default),
            "additionalContext": text,
        }
    })


def run(handler):
    try:
        if not core.disabled():
            payload = core.read_payload(sys.stdin)
            if payload is not None:
                handler(payload)
    except Exception:
        pass
    sys.exit(0)

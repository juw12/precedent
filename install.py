#!/usr/bin/env python3
"""Install precedent into every AI agent on this machine.

Dry run by default: shows exactly what it would change. Nothing is written
until you pass --apply. Every file it edits is backed up once
(<file>.precedent-bak), edits are idempotent, and --uninstall removes only
what precedent added.

    python install.py                      # show the plan
    python install.py --apply              # install for all detected agents
    python install.py --apply --agents codex,opencode
    python install.py --uninstall --apply  # remove everything precedent added
    python install.py --list               # which agents were detected

Agents: Claude Code, Claude Desktop, Codex CLI, OpenCode, Gemini CLI, Cursor,
Windsurf, VS Code. Standard library only.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
MD_START = "<!-- precedent:start -->"
MD_END = "<!-- precedent:end -->"
TOML_START = "# precedent:start"
TOML_END = "# precedent:end"
# event -> (script, matcher, timeout in seconds)
HOOK_SCRIPTS = {
    "UserPromptSubmit": ("prompt_trigger.py", None, 10),
    "PreToolUse": ("integrity_guard.py", "Edit|MultiEdit|Write|Bash", 5),
    "PostToolUse": ("failure_tracker.py", "Bash", 10),
    "PostToolUseFailure": ("failure_tracker.py", "*", 10),
    "Stop": ("stop_guard.py", None, 15),
}
# Codex CLI: Claude-compatible events; no matcher, the scripts filter by tool.
CODEX_HOOK_SCRIPTS = {
    "UserPromptSubmit": ("prompt_trigger.py", None, 10),
    "PreToolUse": ("integrity_guard.py", None, 5),
    "PostToolUse": ("failure_tracker.py", None, 10),
    "Stop": ("stop_guard.py", None, 15),
}
# Gemini CLI: its own event names; timeouts are in milliseconds there.
GEMINI_HOOK_SCRIPTS = {
    "BeforeAgent": ("prompt_trigger.py", None, 10),
    "BeforeTool": ("integrity_guard.py", "run_shell_command|write_file|replace", 5),
    "AfterTool": ("failure_tracker.py", "run_shell_command", 10),
    "AfterAgent": ("stop_guard.py", None, 15),
}


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def home():
    return Path.home()


def install_home():
    return Path(os.environ.get("PRECEDENT_HOME") or home() / ".precedent")


def app_config_dir(name):
    """Per-user app config dir: %APPDATA% on Windows, Application Support on macOS, ~/.config elsewhere."""
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / name
    if sys.platform == "darwin":
        return home() / "Library" / "Application Support" / name
    return Path(os.environ.get("XDG_CONFIG_HOME") or home() / ".config") / name


def xdg_config():
    return Path(os.environ.get("XDG_CONFIG_HOME") or home() / ".config")


def server_path():
    return install_home() / "skills" / "precedent" / "scripts" / "mcp_server.py"


def python_exe():
    return sys.executable


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def backup_once(path):
    if path.is_file():
        backup = path.with_name(path.name + ".precedent-bak")
        if not backup.exists():
            shutil.copy2(path, backup)


def load_json(path):
    """Return (data, problem). Missing file -> ({}, None)."""
    if not path.exists():
        return {}, None
    try:
        text = path.read_text(encoding="utf-8")
        return (json.loads(text) if text.strip() else {}), None
    except (OSError, ValueError):
        return None, "not plain JSON (comments?)"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_once(path)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def remove_block(text, start, end):
    begin = text.find(start)
    if begin == -1:
        return text
    finish = text.find(end, begin)
    if finish == -1:
        return text
    finish += len(end)
    before = text[:begin].rstrip("\n")
    after = text[finish:].lstrip("\n")
    joined = before + ("\n\n" if before and after else ("\n" if before else "")) + after
    return joined


def upsert_block(text, block, start, end):
    cleaned = remove_block(text, start, end)
    wrapped = f"{start}\n{block.strip()}\n{end}\n"
    if not cleaned.strip():
        return wrapped
    return cleaned.rstrip("\n") + "\n\n" + wrapped


# ---------------------------------------------------------------------------
# Operations: each returns a callable(apply, remove) -> status line
# ---------------------------------------------------------------------------


def op_payload():
    def run(apply, remove):
        target = install_home()
        if remove:
            if not target.exists():
                return f"nothing to remove at {target}"
            if apply:
                shutil.rmtree(target, ignore_errors=True)
            return f"remove {target}"
        if apply:
            ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
            for name in ("skills", "hooks", "platforms"):
                shutil.copytree(REPO / name, target / name, dirs_exist_ok=True, ignore=ignore)
            shutil.copy2(REPO / "LICENSE", target / "LICENSE")
        return f"copy precedent to {target}"
    return run


def op_skill(dest):
    def run(apply, remove):
        if remove:
            skill_file = dest / "SKILL.md"
            if not skill_file.is_file() or "name: precedent" not in skill_file.read_text(encoding="utf-8", errors="replace"):
                return f"skill not installed at {dest}"
            if apply:
                shutil.rmtree(dest, ignore_errors=True)
            return f"remove skill {dest}"
        if apply:
            shutil.copytree(
                REPO / "skills" / "precedent",
                dest,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        return f"skill -> {dest}"
    return run


def op_json_entry(path, keys, value_factory):
    """Set or remove data[keys[0]]...[keys[-1]] in a JSON config file."""
    def run(apply, remove):
        data, problem = load_json(path)
        label = f"{path} [{'.'.join(keys)}]"
        if problem:
            return f"SKIP {label}: {problem}; add it manually (see README)"
        node = data
        for key in keys[:-1]:
            if remove and key not in node:
                return f"not present: {label}"
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                return f"SKIP {label}: unexpected structure"
        if remove:
            if keys[-1] not in node:
                return f"not present: {label}"
            if apply:
                del node[keys[-1]]
                write_json(path, data)
            return f"remove {label}"
        value = value_factory()
        if node.get(keys[-1]) == value:
            return f"already set: {label}"
        if apply:
            node[keys[-1]] = value
            write_json(path, data)
        return f"set {label}"
    return run


def op_text_block(path, block, start, end, skip_if=None):
    def run(apply, remove):
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if remove:
            if start not in text:
                return f"not present: block in {path}"
            if apply:
                backup_once(path)
                path.write_text(remove_block(text, start, end), encoding="utf-8")
            return f"remove block from {path}"
        if skip_if and skip_if in remove_block(text, start, end):
            return f"SKIP {path}: already configured outside precedent markers"
        updated = upsert_block(text, block(), start, end)
        if updated == text:
            return f"already set: block in {path}"
        if apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            backup_once(path)
            path.write_text(updated, encoding="utf-8")
        return f"add block to {path}"
    return run


def shell_neutral(path):
    """A path that bash, cmd and PowerShell all read the same way.

    Forward slashes (bash eats backslashes) and no quotes unless needed
    (PowerShell treats a leading quoted string as a value, not a command).
    """
    text = str(path).replace("\\", "/")
    return f'"{text}"' if " " in text else text


def hook_command(script, agent=None):
    hooks_dir = install_home() / "hooks"
    if agent is None:  # Claude Code runs hooks through bash (Git Bash on Windows)
        return f'"{python_exe()}" "{hooks_dir / script}"'
    return f"{shell_neutral(python_exe())} {shell_neutral(hooks_dir / script)} --agent={agent}"


def op_hooks(settings, table, agent=None, timeout_ms=False, named=False):
    """Register precedent's hook scripts in an agent's JSON hook config.

    Same file layout for Claude Code (settings.json), Codex CLI (hooks.json)
    and Gemini CLI (settings.json): {"hooks": {Event: [{matcher?, hooks: [...]}]}}.
    Only entries whose command points into precedent's install dir are touched.
    """
    hooks_dir = install_home() / "hooks"
    markers = (str(hooks_dir), str(hooks_dir).replace("\\", "/"))

    def ours(hook):
        return any(marker in str(hook.get("command", "")) for marker in markers)

    def run(apply, remove):
        data, problem = load_json(settings)
        if problem:
            return f"SKIP {settings}: {problem}"
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            return f"SKIP {settings}: unexpected hooks structure"
        before = json.dumps(hooks, sort_keys=True)
        found = False
        for event, (script, matcher, timeout) in table.items():
            kept = []
            for group in hooks.get(event, []):
                inner = [h for h in group.get("hooks", []) if not ours(h)]
                found = found or len(inner) != len(group.get("hooks", []))
                if inner:
                    kept.append(dict(group, hooks=inner))
            if not remove:
                command = hook_command(script, agent)
                handler = {"type": "command", "command": command, "timeout": timeout * 1000 if timeout_ms else timeout}
                if named:
                    handler["name"] = "precedent-" + script[:-3].replace("_", "-")
                group = {"hooks": [handler]}
                if matcher:
                    group["matcher"] = matcher
                kept.append(group)
            if kept:
                hooks[event] = kept
            elif event in hooks:
                del hooks[event]
        if not hooks:
            data.pop("hooks", None)
        if remove and not found:
            return f"not present: hooks in {settings}"
        if not remove and json.dumps(data.get("hooks", {}), sort_keys=True) == before:
            return f"already set: hooks in {settings}"
        if apply:
            write_json(settings, data)
        return f"{'remove' if remove else 'set'} hooks in {settings}"
    return run


def op_claude_hooks(settings):
    return op_hooks(settings, HOOK_SCRIPTS)


def mcp_stdio():
    return {"command": python_exe(), "args": [str(server_path())]}


def codex_toml_block():
    return (
        "[mcp_servers.precedent]\n"
        f"command = {json.dumps(python_exe())}\n"
        f"args = [{json.dumps(str(server_path()))}]"
    )


def rules_block():
    return (REPO / "platforms" / "rules" / "precedent-rules.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def agents():
    h = home()
    codex = Path(os.environ.get("CODEX_HOME") or h / ".codex")
    opencode = xdg_config() / "opencode"
    vscode_user = app_config_dir("Code") / "User"
    claude_desktop = app_config_dir("Claude")
    return [
        {
            "id": "claude-code",
            "name": "Claude Code",
            "detect": h / ".claude",
            "ops": [
                op_skill(h / ".claude" / "skills" / "precedent"),
                op_json_entry(h / ".claude.json", ["mcpServers", "precedent"], lambda: dict(type="stdio", **mcp_stdio())),
                op_claude_hooks(h / ".claude" / "settings.json"),
            ],
        },
        {
            "id": "claude-desktop",
            "name": "Claude Desktop",
            "detect": claude_desktop,
            "ops": [op_json_entry(claude_desktop / "claude_desktop_config.json", ["mcpServers", "precedent"], mcp_stdio)],
        },
        {
            "id": "codex",
            "name": "Codex CLI",
            "detect": codex,
            "ops": [
                op_skill(codex / "skills" / "precedent"),
                op_text_block(codex / "AGENTS.md", rules_block, MD_START, MD_END),
                op_text_block(codex / "config.toml", codex_toml_block, TOML_START, TOML_END, skip_if="[mcp_servers.precedent]"),
                op_hooks(codex / "hooks.json", CODEX_HOOK_SCRIPTS, agent="codex"),
            ],
        },
        {
            "id": "opencode",
            "name": "OpenCode",
            "detect": opencode,
            "ops": [
                op_skill(opencode / "skills" / "precedent"),
                op_text_block(opencode / "AGENTS.md", rules_block, MD_START, MD_END),
                op_json_entry(
                    opencode / "opencode.json",
                    ["mcp", "precedent"],
                    lambda: {"type": "local", "command": [python_exe(), str(server_path())], "enabled": True},
                ),
            ],
        },
        {
            "id": "gemini",
            "name": "Gemini CLI",
            "detect": h / ".gemini",
            "ops": [
                op_skill(h / ".gemini" / "skills" / "precedent"),
                op_text_block(h / ".gemini" / "GEMINI.md", rules_block, MD_START, MD_END),
                op_json_entry(h / ".gemini" / "settings.json", ["mcpServers", "precedent"], mcp_stdio),
                op_hooks(h / ".gemini" / "settings.json", GEMINI_HOOK_SCRIPTS, agent="gemini", timeout_ms=True, named=True),
            ],
        },
        {
            "id": "cursor",
            "name": "Cursor",
            "detect": h / ".cursor",
            "ops": [op_json_entry(h / ".cursor" / "mcp.json", ["mcpServers", "precedent"], mcp_stdio)],
        },
        {
            "id": "windsurf",
            "name": "Windsurf",
            "detect": h / ".codeium" / "windsurf",
            "ops": [op_json_entry(h / ".codeium" / "windsurf" / "mcp_config.json", ["mcpServers", "precedent"], mcp_stdio)],
        },
        {
            "id": "vscode",
            "name": "VS Code (Copilot agent)",
            "detect": vscode_user,
            "ops": [op_json_entry(vscode_user / "mcp.json", ["servers", "precedent"], lambda: dict(type="stdio", **mcp_stdio()))],
        },
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually write changes (default: dry run)")
    parser.add_argument("--uninstall", action="store_true", help="remove what precedent added")
    parser.add_argument("--agents", help="comma-separated ids, e.g. codex,opencode,cursor")
    parser.add_argument("--list", action="store_true", help="list detected agents and exit")
    args = parser.parse_args()

    selected = {a.strip() for a in args.agents.split(",")} if args.agents else None
    all_agents = agents()
    if selected:
        unknown = selected - {a["id"] for a in all_agents}
        if unknown:
            print(f"Unknown agent id(s): {', '.join(sorted(unknown))}")
            return 2

    detected = [a for a in all_agents if a["detect"].exists() and (not selected or a["id"] in selected)]
    if args.list:
        for agent in all_agents:
            state = "found" if agent["detect"].exists() else "-"
            print(f"{state:6} {agent['id']:15} {agent['name']}  ({agent['detect']})")
        return 0
    if not detected:
        print("No supported agents found. Use the skill folder or platforms/ files manually.")
        return 1

    mode = "UNINSTALL" if args.uninstall else "INSTALL"
    verb = "done" if args.apply else "would"
    print(f"precedent {mode} ({'applying' if args.apply else 'dry run: nothing is written; add --apply'})\n")

    payload = op_payload()
    if args.uninstall:
        for agent in detected:
            print(f"[{agent['name']}]")
            for op in agent["ops"]:
                print(f"  {verb}: {op(args.apply, True)}")
        if not selected:
            print(f"\n  {verb}: {payload(args.apply, True)}")
    else:
        print(f"  {verb}: {payload(args.apply, False)}\n")
        for agent in detected:
            print(f"[{agent['name']}]")
            for op in agent["ops"]:
                print(f"  {verb}: {op(args.apply, False)}")
    if args.apply and not args.uninstall:
        print("\nRestart your agents to load the MCP server, rules and hooks. Backups end in .precedent-bak.")
        if any(agent["id"] == "codex" for agent in detected):
            print("Codex CLI asks you to trust new hooks once: run /hooks inside Codex and approve the precedent ones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Changelog

## 3.0.0: works with every agent

- **Cross-agent precedent search**: reads OpenCode's SQLite history (schema
  checked against a real install) and Codex CLI rollouts, alongside Claude Code,
  shell history and git. Success = completed status / exit code 0.
- **MCP server** (stdio, standard library): `find_precedent`, `review_reply`,
  `record_failure`, `check_test_edit`, `check_command`, plus a `precedent`
  prompt. Works in Cursor, Codex, OpenCode, Gemini CLI, Claude Desktop/Code,
  VS Code and Windsurf.
- **Installer** (`install.py`): detects agents and installs the skill, rules
  (AGENTS.md / GEMINI.md) and MCP config. Dry run by default, backups,
  idempotent, clean uninstall.
- **Better ranking** after testing on real history: only the head of a command
  is matched, words weighted by rarity (IDF), minimum coverage of the query,
  injected system text ignored, near-duplicates merged. 2–4x faster tokenizer.
- 61 automated tests.

## 2.0.0

- **Precedent finder**: searches past Claude Code sessions (successful tool calls
  only, ranked by the task they served), shell history and git log. Used by the
  hooks and available as a CLI (`skills/precedent/scripts/find_precedent.py`).
- **"You did it before" hook** (UserPromptSubmit): hands the agent the action
  that worked last time, or states that none was found so it cannot fake a memory.
- **Stop guard**: give-ups come back with real precedent attached; "done" claims
  with no check after the last edit (or a failing check) are sent back once.
- **Failure tracker**: breaks same-error loops on the 2nd repeat; adds hypotheses
  and precedent on the 3rd and 5th.
- **Integrity guard**: asks before test-weakening edits (skip/xfail/only,
  always-true asserts, removed assertions) and destructive shortcuts.
- Claude Code plugin packaging; Python-only hooks (no bash or jq needed on the
  hook side), secret redaction, `PRECEDENT_OFF=1` switch.
- Cursor rule and a paste-in version for chat apps.
- 40 automated tests with realistic transcripts.

## 1.0.0

- Initial skill: precedent loop, route ladder, verification table, report format.

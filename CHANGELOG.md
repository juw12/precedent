# Changelog

## 3.3.0: OpenCode and GitHub Copilot CLI enforcement, measured on a real agent

- **OpenCode plugin** (`platforms/opencode/precedent.js`), one file for both
  plugin APIs: the V2 default export `{id, setup}` and the classic `server`.
  It blocks test weakening and destructive shortcuts before the tool runs,
  breaks same-error loops, injects the rules into the system prompt and, in
  the TUI or `opencode serve`, sends a give-up or an unchecked "done" back
  once. Verified live on OpenCode 2.2.0: `git push --force` was blocked and
  the bare remote stayed untouched; a give-up was sent back and answered.
- **GitHub Copilot CLI**: skill, MCP entry and its own hook file
  `~/.copilot/hooks/precedent.json` (PascalCase events, top-level
  `permissionDecision`). Built to GitHub's hooks reference; not run live here
  (the test machine's Copilot policy blocks it).
- **Installer**: OpenCode gets an auto-loaded `plugins/precedent/` folder with a
  shim that points at the payload copy. Agents are detected by their CLI on
  PATH or by their own config/history files; a bare skills folder no longer
  counts. A same-named skill that precedent did not install is never
  overwritten or deleted (`.installed-by-precedent` marker).
- **Shell rewrites of tests** (`sed -i`, `perl -i`, `Set-Content`, `Out-File`,
  `truncate`, `>` onto a test file) now get the same check as editor edits.
- Codex and Gemini hook commands use forward-slash paths that every shell
  accepts; Codex output carries only keys its strict schema allows.
- **A/B on a real agent**: `evals/opencode_ab.py` runs the cases through a
  private OpenCode server with and without the plugin and grades each run
  from the final files plus the transcript. Results in `evals/RESULTS.md`.
- **Fixes the A/B found in precedent itself.** The first run showed that the
  "don't give up" push can cause the overreach it is meant to prevent:
  - An honest report of a quoted access denial ("Permission denied", 401,
    Deny-Write ACL) is now evidence, not a give-up, so it is no longer sent
    back. Before, the send-back drove an agent to try removing the ACL.
  - "Keep trying till it works" in an opening request is no longer read as
    pushback on a give-up (that nudge preceded a guessed `admin:admin`).
    Pushback needs an earlier reply; the OpenCode plugin now passes it.
  - Every nudge, the send-back text and the rules now say that a "different
    route" never means guessed or default passwords, secrets found in env vars
    or config files, changing permissions or ACLs, or disabling a check.
  - Two new guard rules: changing permissions to get past a denial
    (`icacls /grant|/remove`, `takeown`, `Set-Acl`, `attrib -r`, `chmod` that
    adds write), and default-credential guessing (`admin:admin`,
    `root:toor`, their Basic-auth forms, `auth=('admin', 'password')`).

## 3.2.0: automatic enforcement in Codex CLI and Gemini CLI

- **Hooks for Codex CLI and Gemini CLI**, not just Claude Code. The same four
  scripts read each agent's payload and transcript (Claude JSONL, Codex
  rollouts, Gemini recordings) and answer in its dialect: end-of-turn guard
  (`Stop` / `AfterAgent`), "you did it before" trigger (`UserPromptSubmit` /
  `BeforeAgent`), loop breaker (`PostToolUse` / `AfterTool`) and integrity
  guard (`PreToolUse` / `BeforeTool`). Payload shapes taken from Codex's
  `hooks/src/schema.rs` and Gemini's hooks reference.
- Codex has no "ask" for tool hooks (it fails open on it), so the integrity
  guard denies with the reason there; Claude Code still asks. Codex
  `apply_patch` envelopes are parsed per file, including test-file deletion.
- Installer writes `~/.codex/hooks.json` and the `hooks` block of
  `~/.gemini/settings.json` (Gemini timeouts in ms), idempotent, clean
  uninstall. Commands are shell-neutral and tested through cmd, PowerShell
  and bash. Codex asks you to trust new hooks once via `/hooks`.
- **`python demo.py`**: four scenes in a throwaway sandbox, no history needed;
  exits non-zero if any scene misbehaves.
- Fixes: garbled "No precedent found" text in the MCP server, stale source
  lists that omitted Gemini CLI, server version out of sync (now one
  `VERSION`), failure signatures redacted before they are stored.

## 3.1.0: checked against real recordings

- **Gemini CLI history** reader (chat recordings, `$set` re-syncs). A shell
  command Gemini marks `success` but that printed `Exit Code: 1` is treated as
  a failure; background commands are skipped.
- **Codex code mode** (0.147+): reads `item_completed` CommandExecution (with
  exit code), McpToolCall and FileChange, and skips the JavaScript `exec`
  wrapper. Content-array outputs handled.
- Both readers run on 21 real Codex rollouts and 36 real Gemini recordings
  from the public cv corpus with no failed command leaking through.
- **Invented precedent check**: `review_reply` flags a command cited on a
  `Precedent:` line that no successful past action contains.
- **Test deletion guard**: `rm`/`git rm`/`Remove-Item` on test files asks first.
- README: before/after example on the first screen, honest tagline.
- 65 automated tests.

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

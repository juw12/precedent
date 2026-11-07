# Changelog

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

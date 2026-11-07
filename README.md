# precedent

**Fewer false "I can't"s. Zero fake "done"s.**

You've seen this: the agent says *"I can't do that"*, you reply *"you did it
before"*, and suddenly it works. The capability was there all along. What it
lacked was a **precedent**.

`precedent` finds that precedent automatically, before the agent gives up, and
makes sure a pushed agent never fakes success to please you.

## What it does

| When | What happens |
|---|---|
| You say "you did it before" / "try again" | The hook searches your past sessions and hands the agent **the exact action that worked last time**, with date and task. If there is none, it tells the agent so, so it can't fake a memory. |
| The agent is about to give up | The answer is sent back once with the precedent attached and a short loop: name the blocker, try three routes that differ in kind, verify. Safety refusals are left alone. |
| The agent says "done" after editing | If nothing ran after the last edit, or the last check failed, it is sent back once to verify. "Should work now" is not a check. |
| The same error happens twice | The agent is told to stop repeating that route; on the 3rd and 5th repeat it gets hypotheses and matching precedent. |
| The agent tries to cheat or cut corners | Adding `skip`/`xfail`/`.only`, `assert True` or removing assertions in a test, and `push --force`, `--no-verify`, `reset --hard`, broad `rm -r` all **ask you first**. |

Example of what the agent receives when it tries to give up:

```
Precedent check: your answer gives up without evidence. Before stopping: ...

Possible precedent found in local history (check it matches before reusing; cite it if you use it):
- [2026-11-01, this project, session 3f2a1b9c; succeeded] Bash: `python -c "import pandas as pd; pd.read_excel('data.xlsx').to_csv('data.csv', index=False)"` (task: "convert data.xlsx to csv")
```

The failed `xlsx2csv` attempt from that same session is **not** shown. Only
actions that succeeded count as precedent.

## Why it's different

- **It searches past sessions automatically.** Other anti-give-up skills tell
  the agent to "check history" or push it with pressure. `precedent` reads your
  Claude Code transcripts, keeps only tool calls that succeeded, and ranks them
  by the task they served. So "deploy the docs" finds `mkdocs gh-deploy` even
  though the two share no words.
- **It is built against fake success.** "Done" needs a check after the last
  edit; test-weakening asks first; the skill forbids faking memories or output.
- **Honest exits.** A well-evidenced `BLOCKED` with one precise ask is a good result.
- **Cross-platform.** The hooks are plain Python 3 (standard library), so
  Windows works without bash or jq on the hook side.
- **Private.** Everything runs locally. No network, no telemetry. Secrets
  (tokens, passwords, keys, `user:pass@` URLs) are redacted from what the agent sees.

## Install

**Claude Code plugin (skill + hooks):**

```
claude plugin marketplace add <your-github-user>/precedent
claude plugin install precedent@precedent
```

**Skill only** (any Agent Skills host: Claude Code, Codex and others): copy
`skills/precedent/` into your skills folder, for example
`~/.claude/skills/precedent/`.

**Cursor:** copy `platforms/cursor/precedent.mdc` into `.cursor/rules/`.

**ChatGPT / Gemini / other chat apps:** paste the block from
`platforms/chatgpt/custom-instructions.md` into custom instructions.

Requirements: Python 3.8+ on PATH as `python3` or `python`.

Turn the hooks off any time with the environment variable `PRECEDENT_OFF=1`.

## Use the precedent finder yourself

```
python skills/precedent/scripts/find_precedent.py "convert xlsx to csv"
python skills/precedent/scripts/find_precedent.py "deploy docs" --git --limit 8
```

It searches Claude Code sessions in `~/.claude/projects`, your shell history
(bash, zsh, PowerShell) and, with `--git`, commit messages in the current repo.

## Results

**Unit and integration:** 40 automated tests feed the hooks realistic Claude Code
transcripts in an isolated home folder (`python -m unittest discover -s tests`).

**Agent A/B:** not yet measured. `evals/` has 13 cases (solvable, impossible,
guardrail) and a scorer that reports solve rate, false-give-up rate,
**fabrication rate** and **guardrail hold rate** side by side. Run it and post
your table, with model, agent and number of runs.

## Limits

- The precedent search is keyword-based. It finds what you did, not what you
  meant; the agent is told to confirm a match before reusing it.
- Claude Code is the only agent with full hook support. Other hosts get the
  skill and the CLI, without automatic enforcement.
- Detection uses phrase patterns, so unusual wording can slip through. Every
  hook fails open: a hook error never blocks you.

## License

MIT

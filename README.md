# precedent

**Stop false "I can't"s and unchecked "done"s, in every AI coding agent.**

You've seen this: the agent says *"I can't do that"*, you reply *"you did it
before"*, and suddenly it works. The capability was there all along. What it
lacked was a **precedent**.

`precedent` finds that precedent automatically, from **all** your agents'
history, before the agent gives up. It also makes sure a pushed agent never
fakes success to please you.

## Before / after

**Without precedent:**

```
You:    convert report.xlsx to csv
Agent:  Sorry, I can't convert Excel files in this environment.
```

**With precedent**, the agent's give-up is sent back with this (real hook output):

```
Precedent check: your answer gives up without evidence. Before stopping: (1) name the
exact blocker; (2) look for precedent ... (3) try at least three routes that differ in
kind; (4) verify any success with a real check. Never invent a past success or fake output.

Possible precedent found in local history (check it matches before reusing; cite it if you use it):
- [2026-11-01, Claude Code, this project, session 3f2a1b9c; succeeded] Bash:
  `python -c "import pandas as pd; pd.read_excel('data.xlsx').to_csv('data.csv', index=False)"`
  (task: "convert data.xlsx to csv")
```

The failed `xlsx2csv` attempt from that same session is not shown: only actions
that **succeeded** count. What the agent does next depends on the agent. The
measured A/B comparison is still to come (see *Verified so far*).

**See it yourself in under a minute**, no agent history needed:

```
python demo.py
```

It builds a throwaway sandbox and runs the real hooks as Claude Code, Codex CLI
and Gemini CLI would: a give-up sent back with last week's working command, a
"done" with no check sent back, and a test-weakening patch denied.

## Works with

| Agent | History searched | How it's enforced |
|---|---|---|
| Claude Code | ✓ transcripts | **automatic hooks** + skill + MCP |
| Codex CLI | ✓ rollouts (incl. 0.161–0.163 code mode) | **automatic hooks** + skill + MCP + AGENTS.md rules |
| Gemini CLI | ✓ chat recordings | **automatic hooks** + skill + MCP + GEMINI.md rules |
| OpenCode | ✓ SQLite history | skill + MCP + AGENTS.md rules |
| Cursor, Windsurf, VS Code (Copilot agent), Claude Desktop | — | MCP server |
| ChatGPT, Gemini app, any chat | — | paste-in rules (`platforms/chatgpt/`) |

Plus shell history (bash, zsh, PowerShell) and git log for everyone.

## Install

```
git clone https://github.com/<you>/precedent
cd precedent
python install.py            # dry run: shows every change, writes nothing
python install.py --apply    # install for every agent it finds
python install.py --uninstall --apply
```

It backs up every file it edits (`*.precedent-bak`), is safe to run twice,
skips any config it can't parse safely (for example JSON with comments) and
tells you what to add by hand. Requirements: Python 3.8+. No other dependencies.
Codex CLI asks you to trust new hooks once: run `/hooks` inside Codex and approve them.

## What it does

| When | What happens |
|---|---|
| You say "you did it before" / "try again" | It searches your past sessions in **every agent**, shell history and git, and hands the agent **the exact action that worked**, with date and task. If there is none, it says so, so the agent can't fake a memory. |
| The agent is about to give up | The answer goes back with the precedent attached and a short loop: name the blocker, try three routes that differ in kind, verify. Safety refusals are left alone. |
| The agent says "done" | If nothing ran after the last edit, or the last check failed, it is sent back to verify. "Should work now" is not a check. |
| The agent cites a precedent | Commands cited on a `Precedent:` line are checked against real history. An invented one is flagged. |
| The same error happens twice | The agent is told to stop repeating that route; on the 3rd and 5th repeat it gets hypotheses and matching precedent. |
| The agent tries to cut corners | Adding `skip`/`xfail`/`.only` or `assert True`, removing assertions, **deleting test files**, `push --force`, `--no-verify`, `reset --hard`, broad `rm -r`: **stopped**. Claude Code asks you; Codex and Gemini have no "ask", so the call is denied with the reason. |

In Claude Code, Codex CLI and Gemini CLI this all happens automatically through
hooks (one set of scripts that speaks each agent's hook format). In other agents
the same checks are MCP tools (`find_precedent`, `review_reply`,
`record_failure`, `check_test_edit`, `check_command`) that the installed rules
tell the agent to use, plus a `precedent` prompt (slash command) where the
client supports MCP prompts.

## Why it's different

- **It searches every agent's history.** It reads Claude Code transcripts,
  OpenCode's database, Codex rollouts and Gemini CLI recordings, keeps only tool
  calls that **succeeded**, and ranks them by the task they served. Each agent
  hides failure differently, and the readers handle it: Gemini marks a shell
  command that exits 1 as `success` with `Exit Code: 1` in its output, and Codex
  code mode wraps commands in JavaScript.
- **It is built against fake success.** "Done" needs a check after the last
  change, invented precedents are flagged, and weakening or deleting tests asks first.
- **Honest exits.** A well-evidenced `BLOCKED` with one precise ask is a good result.
- **Private.** Everything runs locally: no network, no telemetry. Databases are
  opened read-only. Tokens, passwords, keys and `user:pass@` URLs are redacted.

## Use the finder yourself

```
python skills/precedent/scripts/find_precedent.py "convert xlsx to csv"
python skills/precedent/scripts/find_precedent.py "deploy docs" --git --limit 8
```

## Manual setup (if you skip the installer)

- **Skill:** copy `skills/precedent/` into your agent's skills folder.
- **MCP server:** command `python`, args `["<path>/skills/precedent/scripts/mcp_server.py"]` (stdio).
- **Rules:** paste `platforms/rules/precedent-rules.md` into AGENTS.md / GEMINI.md / your rules.
- **Cursor rule:** `platforms/cursor/precedent.mdc` into `.cursor/rules/`.
- **Claude Code plugin:** `claude plugin marketplace add <you>/precedent`, then `claude plugin install precedent@precedent`.

Turn the hooks off any time with `PRECEDENT_OFF=1`.

## Verified so far

- **88 automated tests**: hooks fed realistic transcripts, an OpenCode
  database with the real schema, Codex and Gemini fixtures copied from real
  recordings, Codex and Gemini hook payloads in the shapes their sources define,
  a live MCP stdio session, the demo, and the installer against a fake home
  with real existing configs (dry run writes nothing, re-runs change nothing,
  uninstall removes only what it added; installed hook commands run through
  cmd, PowerShell and bash). Run: `python -m unittest discover -s tests`.
- **Real recordings**: the Codex reader was run on 21 real rollouts (Codex
  0.161, 0.162, 0.163) and the Gemini reader on 36 real recordings (Gemini CLI
  0.46, 0.63) from the public [cv](https://github.com/emberian/cv) test corpus.
  Every failed or backgrounded command was excluded, and both returned the right
  commands for a "replace TODO with DONE in notes.txt" query.
- **Real history**: tested read-only against a real OpenCode history of about
  300k records. Searches take 1–3 s, and a query with no real precedent returns nothing.
- **Agent A/B**: not yet measured. `evals/` has 13 cases and a scorer for solve
  rate, false-give-up rate, **fabrication rate** and **guardrail hold rate**.

## Limits

- The search is keyword-based (IDF-weighted). It can surface a near-miss, so
  results are labelled "possible precedent" and the agent is told to confirm a
  match before reusing it.
- Automatic hooks cover Claude Code, Codex CLI and Gemini CLI. OpenCode,
  Cursor, Windsurf, VS Code and Claude Desktop get the same checks as MCP tools
  plus rules, so there they depend on the agent calling them.
- The Codex and Gemini hook wiring is tested against the payload shapes in
  their source (Codex `hooks/src/schema.rs`) and docs (Gemini hooks reference),
  not yet inside a live Codex or Gemini session.
- Codex rollouts older than 7 days may be zstd-compressed (`.jsonl.zst`); those
  are skipped (no standard-library decoder). Antigravity's protobuf
  conversations are not read.
- Detection uses phrase patterns, so unusual wording can slip through. Every
  hook and tool fails open: an error never blocks you.

## License

MIT

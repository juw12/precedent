# precedent

**Fewer false "I can't"s. Zero fake "done"s. In every AI agent.**

You've seen this: the agent says *"I can't do that"*, you reply *"you did it
before"*, and suddenly it works. The capability was there all along. What it
lacked was a **precedent**.

`precedent` finds that precedent automatically, from **all** your agents'
history, before the agent gives up. It also makes sure a pushed agent never
fakes success to please you.

## Works with

| Agent | How |
|---|---|
| Claude Code | skill + MCP + automatic hooks (strongest) |
| Codex CLI | skill + MCP + AGENTS.md rules |
| OpenCode | skill + MCP + AGENTS.md rules |
| Gemini CLI | skill + MCP + GEMINI.md rules |
| Cursor, Windsurf, VS Code (Copilot agent), Claude Desktop | MCP server |
| ChatGPT, Gemini app, any chat | paste-in rules (`platforms/chatgpt/`) |

One installer sets up everything it finds:

```
git clone https://github.com/<you>/precedent
cd precedent
python install.py            # dry run: shows every change, writes nothing
python install.py --apply    # install
python install.py --uninstall --apply
```

It backs up every file it edits (`*.precedent-bak`), is safe to run twice,
skips any config it can't parse safely (for example JSON with comments) and
tells you what to add by hand. Requirements: Python 3.8+. No other dependencies.

## What it does

| When | What happens |
|---|---|
| You say "you did it before" / "try again" | It searches your past **Claude Code, OpenCode and Codex** sessions, shell history and git, and hands the agent **the exact action that worked**, with the date and task. If there is none, it says so, so the agent can't fake a memory. |
| The agent is about to give up | The answer goes back with the precedent attached and a short loop: name the blocker, try three routes that differ in kind, verify. Safety refusals are left alone. |
| The agent says "done" | If nothing ran after the last edit, or the last check failed, it is sent back to verify. "Should work now" is not a check. |
| The same error happens twice | The agent is told to stop repeating that route; on the 3rd and 5th repeat it gets hypotheses and matching precedent. |
| The agent tries to cut corners | Adding `skip`/`xfail`/`.only` or `assert True`, removing assertions, `push --force`, `--no-verify`, `reset --hard` or broad `rm -r`: **you are asked first**. |

In Claude Code this all happens automatically through hooks. In other agents
the same checks are MCP tools (`find_precedent`, `review_reply`,
`record_failure`, `check_test_edit`, `check_command`) that the installed rules
tell the agent to use, plus a `precedent` prompt (slash command) where the
client supports MCP prompts.

## Why it's different

- **It searches every agent's history.** It reads your Claude Code transcripts,
  OpenCode's database and Codex rollouts, keeps only tool calls that
  **succeeded** (exit code 0 or completed status), and ranks them by the task
  they served. Rare words count more than common ones, so path noise like
  `AppData` doesn't drown the signal.
- **It is built against fake success.** "Done" needs a check after the last
  change, test-weakening asks first, and faking memories or output is forbidden.
- **Honest exits.** A well-evidenced `BLOCKED` with one precise ask is a good result.
- **Private.** Everything runs locally: no network, no telemetry. Databases are
  opened read-only. Tokens, passwords, keys and `user:pass@` URLs are redacted
  from what the agent sees.

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

- **61 automated tests**: hooks fed realistic transcripts, an OpenCode
  database with the real schema, a Codex rollout in the documented format, a
  live MCP stdio session, and the installer run against a fake home with real
  existing configs (dry run writes nothing, re-runs change nothing, uninstall
  removes only what it added). Run: `python -m unittest discover -s tests`.
- **Real data**: tested read-only against a real OpenCode history (300k
  records). Searches take 1–3 s, and a query with no real precedent returns nothing.
- **Agent A/B**: not yet measured. `evals/` has 13 cases and a scorer for solve
  rate, false-give-up rate, **fabrication rate** and **guardrail hold rate**.

## Limits

- The search is keyword-based (IDF-weighted). It can surface a near-miss, so
  results are labelled "possible precedent" and the agent is told to confirm a
  match before reusing it.
- Automatic enforcement (hooks) is Claude Code only. Other agents get the
  same checks as MCP tools plus rules, so they depend on the agent calling them.
- The Codex reader follows the documented rollout format and has not yet been
  checked against real Codex sessions. Gemini CLI history isn't read yet.
- Detection uses phrase patterns, so unusual wording can slip through. Every
  hook and tool fails open: an error never blocks you.

## License

MIT

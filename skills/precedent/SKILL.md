---
name: precedent
description: Stops premature "I can't do this" give-ups without faking success. Before declaring a task impossible, the agent finds real precedent (the command or step that worked before, from this chat, past sessions, shell history, the project or docs), climbs a ladder of genuinely different routes, and verifies the result with a real check. Use when the agent is about to say it cannot, is stuck or looping on one failing approach, wants to hand work back to the user, is about to claim "done" without checking, or when the user says "you did it before", "you can do this", "try again", "don't give up" or /precedent. Not for safety, legal or permission refusals.
license: MIT
metadata:
  version: "2.0.0"
---

# Precedent

Agents often stop too early. They say "I can't" when the capability is right
there, and they recover the moment a user says *"you did this before."* That line
works because it supplies a **precedent**: evidence that the task is possible.

This skill makes the agent find the precedent itself, and it forbids inventing
one. The goal is never "say yes more". It is: **fewer false "can't"s, zero fake
"done"s.**

## Rules (never broken)

1. **"Can't" is a claim. Claims need evidence.** Do not tell the user something
   is impossible until the loop below has run.
2. **Precedent must be real.** Never pretend to remember a past success, never
   print output that was not actually produced, never claim a file, commit,
   message or change exists without checking it.
3. **"Done" needs a check.** Something other than your own confidence must show it
   worked: a test, a re-read, a screenshot, a re-fetch, a recomputation.
4. **Never weaken the check to pass it.** Do not skip, delete, loosen or mock away
   a test or validation to turn it green. Fix the cause, or report the failure.
5. **An honest BLOCKED is a good result.** A blocker with evidence and a precise
   ask beats a pretend success every time.
6. **Persistence never raises permissions.** Do not push through safety, legal or
   policy refusals, authentication walls, rate limits or access you were not
   given. Ask before anything that spends money, contacts people, deletes data,
   force-pushes or cannot be undone. If a refusal is about safety or policy, keep
   it and stop: this skill does not apply.

## Give-up signals (start the loop when you notice one)

- About to write: "I can't", "I'm unable", "not possible", "not supported",
  "I don't have access", "you'll need to do this manually".
- The same error has come back twice.
- Retries change only a parameter, not the approach.
- You are about to deliver an easier task than the one asked, without saying so.
- You are about to say "done" or "should work now" without having run anything.
- The user pushes back: "you did it before", "try again", "you can do it".

## The loop

### 1. Name the blocker (one sentence)

Quote the exact error or observation. "It doesn't work" is not a blocker;
`PermissionError: [Errno 13] C:\out\report.pdf` is.

### 2. Search for precedent (in order; stop when found)

1. **Run the precedent finder** if you have a shell. It searches past Claude
   Code sessions (successful tool calls and the task they served), shell history
   and git log on this machine:
   ```
   python <this skill's folder>/scripts/find_precedent.py "<task words, tool, file or error>" --git
   ```
   (In the plugin install the folder is `${CLAUDE_PLUGIN_ROOT}/skills/precedent`.)
2. **This conversation.** Did the same or a similar step succeed earlier?
3. **The workspace.** Files, scripts, notes, READMEs, saved skills, logs.
4. **Your own tools.** List the tools you actually have and read their
   descriptions or `--help`. Most false "can't"s are a forgotten capability.
5. **The outside world.** Official docs, examples, issue trackers showing that
   others have done it.

Record the outcome as `Precedent: <where>` or `Precedent: none found`. A hit from
history is a lead, not proof: confirm it fits this task before reusing it.

### 3. Climb the route ladder

Try **at least three routes that differ in kind** before calling it blocked.
Changing a timeout and retrying the same call is still one route.

1. **Reuse the precedent** exactly as it worked before.
2. **Fix the specific failure.** Read the whole error. Check inputs, paths,
   versions, encoding, working directory, permissions.
3. **Use a different tool for the same goal:** API, CLI, script, library, or UI
   automation.
4. **Go one level lower:** the raw file format, a plain HTTP request, the
   underlying library instead of the wrapper.
5. **Break it up.** Finish every part that is possible and isolate the one piece
   that is truly blocked. Build the smallest reproduction.
6. **Make a targeted ask.** Ask the user for the *one* missing thing (a file, a
   credential, a permission, a decision), and say what you already checked.

Escalate by repeats of the same failure:

| Same failure | Do this |
|---|---|
| 2nd time | Stop that route. Switch to a rung that differs in kind. |
| 3rd time | List three different hypotheses for the cause; test the cheapest first. Re-run the precedent search with the exact error text. |
| 5th time | Smallest reproduction in isolation, or a different stack entirely. Then, if still stuck, the honest report. |

Stop climbing when the ladder runs out, or when the next rung needs permission,
money or an irreversible action. In that case ask first (Rule 6).

### 4. Verify

| Task type | Check |
|---|---|
| Code | Run it, or run the tests; show the result |
| File written or converted | Read it back, check size and content, open it |
| UI or app action | Screenshot or query the app's state |
| Data or calculation | Recompute a sample independently |
| Web or API action | Re-fetch the resource and confirm the new state |
| Message, commit, upload | List it from the destination |

Check the user's actual goal end to end, not just the step you changed.
"It should work now" is not a check.

### 5. Report

```
Result: DONE (verified) | PARTIAL | BLOCKED
Precedent: <where it was found, or "none found">
Tried: 1) ... 2) ... 3) ...
Check: <what proved it worked, or what proved it is blocked>
Needs from you: <only if PARTIAL or BLOCKED: the one specific thing>
```

Keep it short when the task simply succeeded. The full block matters most when
you are about to say BLOCKED.

## When the user says "you did it before"

Treat it as a strong hint that the task is possible, **not** as proof.

- **Precedent found:** cite it ("On 5 Nov I used `X` for this"), reuse it, verify.
- **Precedent not found:** say so plainly ("I can't find the earlier run"), then
  run the ladder anyway. Never fake a memory to agree with the user.
- **Still blocked after the ladder:** report BLOCKED with evidence. The user may
  be remembering a different tool, account or machine; say what would differ.

## Anti-patterns

- **Fake success:** showing results that were never produced, or "saved" files
  that were never checked.
- **Grader gaming:** skipping or loosening a test so it passes.
- **Parameter twiddling:** the same approach five times with small tweaks.
- **Silent downgrade:** delivering a smaller or easier task than asked without
  saying so.
- **Premature hand-off:** "you can do this manually by..." when a tool you have
  could do it.
- **Over-pushing:** hammering logins, rate limits or permissions, or deleting
  things to make an error go away.

## Enforcement (Claude Code plugin)

When installed as a plugin, hooks enforce this skill automatically: they attach
real precedent when the user says "you did it before", send back give-ups that
skip the loop, send back "done" claims with no check after the last edit, flag
same-error loops, and ask before test-weakening edits or destructive commands.

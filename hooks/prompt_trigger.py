#!/usr/bin/env python3
"""UserPromptSubmit hook: turns "you did it before" into actual evidence.

When the user says the agent did this before, or pushes back ("try again",
"you can do it"), search local history for the successful action and hand it
to the agent, or tell it plainly that none was found so it cannot fake a memory.
"""

from _bootstrap import core, emit, run

CLAIM_FOUND = (
    "[precedent] The user says this was done before. Local history has possibly "
    "matching actions that succeeded (listed below). Check which one fits this task, "
    "reuse it, verify the result, and cite where it came from."
)
CLAIM_NOT_FOUND = (
    "[precedent] The user says this was done before, but no matching successful "
    "action was found in local history (Claude Code sessions for this machine, shell "
    "history). Do not claim to remember it. Treat the claim as a hint that the task is "
    "possible: name the blocker, search the workspace and your tool list, try at least "
    "three routes that differ in kind, and verify. If still blocked, say what you searched."
)
PUSHBACK = (
    "[precedent] The user is pushing back on a give-up. Do not repeat the same attempt. "
    "Name the exact blocker, look for precedent, then try a route that differs in kind "
    "(different tool, lower level, break it up). Verify any success with a real check."
)


def handle(payload):
    prompt = str(payload.get("prompt") or "")
    is_claim = core.matches_any(core.PRECEDENT_CLAIM, prompt)
    is_pushback = core.matches_any(core.PUSHBACK, prompt)
    if not (is_claim or is_pushback):
        return

    transcript = payload.get("transcript_path")
    events = core.parse_transcript(transcript) if transcript else []
    query = " ".join([prompt, core.recent_user_text(events, 2), core.last_assistant_text(events)])
    hits = core.find_precedents(query, current_path=transcript, cwd=payload.get("cwd"), limit=4)

    if is_claim:
        context = CLAIM_FOUND if hits else CLAIM_NOT_FOUND
    else:
        context = PUSHBACK
    if hits:
        context += "\n" + core.format_hits(hits)

    emit({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    })


if __name__ == "__main__":
    run(handle)

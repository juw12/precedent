#!/usr/bin/env python3
"""Stop hook: no unproven "can't", no unverified "done".

1. A final answer that gives up without the precedent report is sent back
   once, with any matching precedent from local history attached.
2. A final answer that claims "done" after editing files, with no check after
   the last edit (or a failing one), is sent back once to verify.

Never blocks twice in a row (respects stop_hook_active) and leaves safety or
policy refusals alone.
"""

from _bootstrap import core, emit, run

GIVE_UP_REASON = (
    "Precedent check: your answer gives up without evidence. Before stopping: "
    "(1) name the exact blocker; (2) look for precedent in this conversation, the "
    "workspace, your tool list and the docs (the matches below are a start); "
    "(3) try at least three routes that differ in kind; (4) verify any success "
    "with a real check. Never invent a past success or fake output. If it is still "
    "blocked, answer with: Result / Precedent / Tried / Check / Needs from you. "
    "If this was a safety, legal or policy refusal, keep it and simply stop."
)


def handle(payload):
    if payload.get("stop_hook_active"):
        return
    transcript = payload.get("transcript_path")
    if not transcript:
        return

    events = core.parse_transcript(transcript)
    text = core.last_assistant_text(events)
    turns = core.split_turns(events)
    turn = turns[-1] if turns else []

    if core.is_give_up(text):
        query = core.recent_user_text(events, 3) + " " + text
        hits = core.find_precedents(query, current_path=transcript, cwd=payload.get("cwd"), limit=4)
        emit({"decision": "block", "reason": GIVE_UP_REASON + core.precedent_block(hits)})
        return

    problem = core.unverified_done_problem(text, turn)
    if problem:
        emit({"decision": "block", "reason": problem})


if __name__ == "__main__":
    run(handle)

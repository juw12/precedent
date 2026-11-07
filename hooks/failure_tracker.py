#!/usr/bin/env python3
"""PostToolUse / PostToolUseFailure hook: break same-error loops early.

Counts failures per session by a normalized error signature.
- 2nd identical failure: stop repeating, switch to a route that differs in kind.
- 3rd and 5th identical failure: also list hypotheses and attach precedent.
- 4th and 8th failure overall: pause and run the full precedent loop.
Silent otherwise. Successful tools never reset the count: a passing `ls` is
not evidence the task works.
"""

from _bootstrap import core, emit, run


def handle(payload):
    event = payload.get("hook_event_name") or "PostToolUse"
    tool = str(payload.get("tool_name") or "")
    failed, error_text = core.tool_failure(event, payload)
    if not failed:
        return

    session = str(payload.get("session_id") or "default")
    notes, repeats, total, _ = core.register_failure(session, tool, error_text)
    if not notes:
        return

    if repeats >= 3 or total in (4, 8):
        transcript = payload.get("transcript_path")
        events = core.parse_transcript(transcript) if transcript else []
        query = " ".join([str(error_text)[:400], core.recent_user_text(events, 2)])
        hits = core.find_precedents(query, current_path=transcript, cwd=payload.get("cwd"), limit=3)
        if hits:
            notes.append("Possible precedent:\n" + core.format_hits(hits))

    emit({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": "\n".join(notes),
        }
    })


if __name__ == "__main__":
    run(handle)

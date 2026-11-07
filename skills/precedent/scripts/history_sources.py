"""History readers for agents other than Claude Code.

- OpenCode: SQLite at ~/.local/share/opencode/opencode.db (or $XDG_DATA_HOME).
  Schema checked against a real install: table `part` holds JSON with
  type "tool", "tool" (name), state.status ("completed"/"error"),
  state.input, state.metadata.exit; user text lives in `part` rows of type
  "text" whose `message` row has role "user".
- Codex CLI: JSONL rollouts at ~/.codex/sessions/**/rollout-*.jsonl (also
  listed in state_*.sqlite threads.rollout_path). Envelope {timestamp, type,
  payload}; payload types message / function_call / function_call_output /
  local_shell_call / custom_tool_call. Built from the documented format; not
  yet checked against real rollouts.

Every reader yields candidates in the shape precedent_core.score() expects
and never raises: unreadable or unknown data is skipped.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

LOOKUP_TOOLS = {
    "read", "glob", "grep", "list", "ls", "todowrite", "todoread", "task", "skill",
    "background_output", "view", "update_plan",
}
EXIT_CODE = re.compile(r"(?:Exit code:?|exited with code|exit status)\s*(-?\d+)", re.IGNORECASE)


def _date_from_ms(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _norm(path):
    return os.path.normcase(os.path.normpath(path)) if path else ""


def _turn_candidates(turns, agent, session_label, same_project, when_of, summarize):
    """Turn [(task, [success_events])] into candidates; marks the last success per tool."""
    for task, uses in turns:
        last_index = {}
        for index, use in enumerate(uses):
            last_index[use["name"]] = index
        for index, use in enumerate(uses):
            summary = summarize(use["name"], use["input"])
            if not summary:
                continue
            yield {
                "source": agent.lower().replace(" ", "-"),
                "agent": agent,
                "session": session_label,
                "same_project": same_project,
                "when": when_of(use),
                "tool": use["name"],
                "summary": summary,
                "task": task,
                "final_in_turn": last_index[use["name"]] == index,
                "outcome": "succeeded",
            }


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------


def opencode_db_path():
    override = os.environ.get("PRECEDENT_OPENCODE_DB")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "opencode" / "opencode.db"


def _connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def _opencode_parts(con, max_parts):
    """Recent parts as tuples, newest last. Uses SQLite JSON1 when available."""
    try:
        rows = con.execute(
            "select rowid, session_id, message_id, time_created, "
            "json_extract(data, '$.type'), json_extract(data, '$.tool'), "
            "json_extract(data, '$.state.status'), json_extract(data, '$.state.input'), "
            "json_extract(data, '$.state.metadata.exit'), json_extract(data, '$.text'), "
            "json_extract(data, '$.synthetic') "
            "from part order by rowid desc limit ?",
            (max_parts,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
        for rowid, sid, mid, created, data in con.execute(
            "select rowid, session_id, message_id, time_created, data from part order by rowid desc limit ?",
            (max_parts,),
        ):
            try:
                d = json.loads(data)
            except (TypeError, ValueError):
                continue
            state = d.get("state") or {}
            rows.append((
                rowid, sid, mid, created, d.get("type"), d.get("tool"), state.get("status"),
                json.dumps(state.get("input")) if state.get("input") is not None else None,
                (state.get("metadata") or {}).get("exit"), d.get("text"), d.get("synthetic"),
            ))
    rows.reverse()
    return rows


def _chunks(items, size=400):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _opencode_roles(con, message_ids):
    roles = {}
    for chunk in _chunks(message_ids):
        marks = ",".join("?" * len(chunk))
        try:
            query = f"select id, json_extract(data, '$.role') from message where id in ({marks})"
            for mid, role in con.execute(query, chunk):
                roles[mid] = role
        except sqlite3.OperationalError:
            for mid, data in con.execute(f"select id, data from message where id in ({marks})", chunk):
                try:
                    roles[mid] = json.loads(data).get("role")
                except (TypeError, ValueError):
                    pass
    return roles


def _opencode_directories(con, session_ids):
    directories = {}
    for table in ("session", "session_v2"):
        for chunk in _chunks(session_ids):
            marks = ",".join("?" * len(chunk))
            try:
                for sid, directory in con.execute(f"select id, directory from {table} where id in ({marks})", chunk):
                    directories.setdefault(sid, directory)
            except sqlite3.Error:
                break
    return directories


def opencode_candidates(summarize, cwd=None, max_parts=20000):
    db = opencode_db_path()
    if not db.is_file():
        return
    try:
        con = _connect_readonly(db)
    except sqlite3.Error:
        return
    try:
        parts = _opencode_parts(con, max_parts)
        text_messages = {p[2] for p in parts if p[4] == "text"}
        roles = _opencode_roles(con, text_messages)
        directories = _opencode_directories(con, {p[1] for p in parts})
    except sqlite3.Error:
        con.close()
        return
    con.close()

    sessions = {}
    for rowid, sid, mid, created, ptype, tool, status, raw_input, exit_code, text, synthetic in parts:
        turns = sessions.setdefault(sid, [])
        if ptype == "text" and roles.get(mid) == "user" and text and not synthetic and not is_injected(text):
            turns.append([str(text), []])
            continue
        if ptype != "tool" or not tool or not turns:
            continue
        name = str(tool)
        if name.lower() in LOOKUP_TOOLS:
            continue
        if status != "completed" or (exit_code not in (None, 0, "0")):
            continue
        try:
            tool_input = json.loads(raw_input) if isinstance(raw_input, str) else (raw_input or {})
        except ValueError:
            tool_input = {}
        turns[-1][1].append({"name": name, "input": tool_input if isinstance(tool_input, dict) else {}, "ts": created})

    cwd_norm = _norm(cwd)
    for sid, turns in sessions.items():
        same_project = bool(cwd_norm) and _norm(directories.get(sid)) == cwd_norm
        yield from _turn_candidates(
            [(task, uses) for task, uses in turns if uses],
            "OpenCode",
            str(sid)[-8:],
            same_project,
            lambda use: _date_from_ms(use["ts"]),
            summarize,
        )


# ---------------------------------------------------------------------------
# Codex CLI
# ---------------------------------------------------------------------------

CODEX_NOISE = ("<environment_context>", "<user_instructions>", "<permissions", "# AGENTS.md", "<INSTRUCTIONS>")


def is_injected(text):
    """System or harness text injected as a 'user' message, not a real request."""
    stripped = str(text).lstrip()
    return stripped.startswith("<") or stripped.startswith(CODEX_NOISE)


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_files(max_files=60):
    home = codex_home()
    files = set()
    for pattern in ("sessions/**/*.jsonl", "archived_sessions/**/*.jsonl"):
        files.update(home.glob(pattern))
    for db in home.glob("state_*.sqlite"):
        try:
            con = _connect_readonly(db)
            for (rollout,) in con.execute("select rollout_path from threads where rollout_path is not null"):
                files.add(Path(rollout))
            con.close()
        except sqlite3.Error:
            pass
    existing = [f for f in files if f.is_file()]
    existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return existing[:max_files]


def _codex_command(value):
    if isinstance(value, list):
        parts = [str(v) for v in value]
        if len(parts) >= 3 and parts[1] in ("-lc", "-c", "/c", "-Command"):
            return parts[-1]
        return " ".join(parts)
    return str(value) if value else ""


def _codex_output_ok(output, failure_text):
    if isinstance(output, dict):
        output = output.get("content") or output.get("output") or json.dumps(output)
    text = str(output or "")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            code = (data.get("metadata") or {}).get("exit_code")
            if code is not None:
                return code == 0
            text = str(data.get("output", text))
    except ValueError:
        pass
    match = EXIT_CODE.search(text[:2000])
    if match:
        return match.group(1) == "0"
    return not failure_text.search(text[:1500])


def codex_session_candidates(path, summarize, failure_text, cwd=None):
    from precedent_core import read_jsonl

    session_cwd = ""
    turns = []
    pending = {}
    first_stamp = ""
    for entry in read_jsonl(path):
        item = entry.get("payload") if isinstance(entry.get("payload"), dict) else entry
        first_stamp = first_stamp or str(entry.get("timestamp", ""))[:10]
        kind = item.get("type") or entry.get("type")
        if entry.get("type") == "session_meta" or kind == "session_meta":
            session_cwd = item.get("cwd") or session_cwd
            continue
        if kind == "message" and item.get("role") == "user":
            texts = [
                str(c.get("text", "")) for c in item.get("content") or []
                if isinstance(c, dict) and c.get("type") in ("input_text", "text")
            ]
            text = "\n".join(t for t in texts if t.strip())
            if text and not is_injected(text):
                turns.append([text, []])
            continue
        if kind in ("function_call", "local_shell_call", "custom_tool_call"):
            if kind == "function_call":
                name = str(item.get("name", "tool"))
                try:
                    tool_input = json.loads(item.get("arguments") or "{}")
                except ValueError:
                    tool_input = {"input": str(item.get("arguments"))}
            elif kind == "local_shell_call":
                name = "shell"
                tool_input = dict((item.get("action") or {}))
            else:
                name = str(item.get("name", "tool"))
                tool_input = {"input": str(item.get("input", "")).strip().splitlines()[0] if item.get("input") else ""}
            if not isinstance(tool_input, dict):
                tool_input = {"input": str(tool_input)}
            if "command" in tool_input:
                tool_input["command"] = _codex_command(tool_input["command"])
            call_id = item.get("call_id") or item.get("id")
            if turns and call_id:
                pending[call_id] = (len(turns) - 1, {"name": name, "input": tool_input, "ts": entry.get("timestamp", "")})
            continue
        if kind in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
            call_id = item.get("call_id") or item.get("id")
            if call_id in pending and _codex_output_ok(item.get("output"), failure_text):
                turn_index, use = pending.pop(call_id)
                if use["name"].lower() not in LOOKUP_TOOLS:
                    turns[turn_index][1].append(use)

    same_project = bool(cwd) and _norm(session_cwd) == _norm(cwd)
    yield from _turn_candidates(
        [(task, uses) for task, uses in turns if uses],
        "Codex",
        Path(path).stem[-8:],
        same_project,
        lambda use: str(use.get("ts", ""))[:10] or first_stamp,
        summarize,
    )


def codex_candidates(summarize, failure_text, cwd=None, deadline=None):
    import time

    for path in codex_files():
        if deadline is not None and time.monotonic() > deadline:
            return
        yield from codex_session_candidates(path, summarize, failure_text, cwd=cwd)

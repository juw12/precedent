"""Shared engine for the precedent hooks and the find_precedent CLI.

Standard library only. Local only: reads Claude Code transcripts, shell
history and git logs on this machine; never touches the network.
"""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Text signals
# ---------------------------------------------------------------------------


def _compile(patterns, flags=re.IGNORECASE):
    """Compile patterns, accepting straight and curly apostrophes alike."""
    return [re.compile(p.replace("'", "['\u2019]"), flags) for p in patterns]


GIVE_UP = _compile([
    r"\bI(?: can'?t| cannot| am unable to|'m unable to| am not able to|'m not able to)\b",
    r"\b(?:is|it's|that's|this is) (?:not possible|impossible)\b",
    r"\bnot (?:currently )?supported\b",
    r"\bI don'?t have (?:access|the ability|permission)\b",
    r"\boutside (?:of )?my (?:capabilities|abilities)\b",
    r"\byou(?:'ll| will) (?:need|have) to (?:do|run|perform|complete) (?:this|it|that) (?:manually|yourself)\b",
    r"\bbeyond what I can do\b",
    r"\bthere(?:'s| is) no way (?:for me )?to\b",
])

SAFETY = _compile([
    r"\b(?:harmful|illegal|unsafe|dangerous|malicious|unethical)\b",
    r"\b(?:policy|policies|guidelines)\b",
    r"\bcan'?t help with (?:that|this) request\b",
    r"\bnot comfortable\b",
])

REPORT_MARKERS = _compile([r"^\s*\**Precedent:", r"^\s*\**Tried:"], re.IGNORECASE | re.MULTILINE)

DONE_CLAIM = _compile([
    r"\b(?:it's|that's|everything's|everything is|it is|that is|all) (?:done|fixed|working|set|complete)\b",
    r"\bI(?:'ve| have) (?:fixed|resolved|completed|finished|implemented|updated|added|changed)\b",
    r"\bshould (?:now )?(?:work|be (?:fixed|working|resolved))\b",
    r"\bworks? (?:now|correctly|as expected)\b",
    r"^\s*\**(?:done|fixed|all set)\b",
    r"\bsuccessfully (?:fixed|updated|implemented|resolved|completed|added)\b",
], re.IGNORECASE | re.MULTILINE)

HONEST_UNVERIFIED = _compile([
    r"\bunverified\b",
    r"\buntested\b",
    r"\bnot (?:yet )?(?:verified|tested|run)\b",
    r"\b(?:haven't|have not|couldn't|could not|didn't|did not|wasn't able to|was not able to) (?:verify|test|run)\b",
])

PRECEDENT_CLAIM = _compile([
    r"\b(?:you|u) (?:did|done|made|ran|managed|fixed|have done|'ve done|had done|already did)\b[^.?!]{0,30}\b(?:before|earlier|last time|yesterday|already|previously|last week)\b",
    r"\b(?:it|this|that) worked (?:before|earlier|last time|yesterday|previously)\b",
    r"\blast time (?:you|u|it|we)\b",
    r"\bwe(?:'ve| have)? (?:done|did) (?:it|this|that) before\b",
    r"\b(?:you|u)(?:'ve| have)? (?:done|did) (?:it|this|that) (?:many|so many|lots of|plenty of) times\b",
])

PUSHBACK = _compile([
    r"\btry again\b",
    r"\bkeep trying\b",
    r"\bdon'?t give up\b",
    r"\b(?:you|u) can do (?:it|this|that)\b",
    r"\b(?:it's|it is) (?:not impossible|possible)\b",
    r"\bfigure it out\b",
    r"\bstop saying (?:you )?can'?t\b",
    r"\byes (?:you|u) can\b",
])


def matches_any(patterns, text):
    return any(p.search(text or "") for p in patterns)


def is_give_up(text):
    """Gives up, without the precedent report, and is not a safety refusal."""
    if not text or not matches_any(GIVE_UP, text):
        return False
    if matches_any(REPORT_MARKERS, text):
        return False
    if matches_any(SAFETY, text):
        return False
    return True


def is_done_claim(text):
    return bool(text) and matches_any(DONE_CLAIM, text) and not matches_any(HONEST_UNVERIFIED, text)


# ---------------------------------------------------------------------------
# Transcripts (Claude Code JSONL)
# ---------------------------------------------------------------------------

EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
LOOKUP_TOOLS = {"Read", "Glob", "Grep", "LS", "TodoWrite", "Task", "TaskCreate", "ExitPlanMode"}
NOISE_PREFIXES = ("<command-", "<local-command", "Caveat:", "[Request interrupted", "<system-reminder")
FAILURE_TEXT = re.compile(
    r"Traceback \(most recent call last\)|\bExit code [1-9]\d*|command not found|"
    r"is not recognized as (?:an internal|the name)|^\s*(?:Error|error|ERROR)\b[:\s]|"
    r"No such file or directory|Permission denied|Access is denied",
    re.MULTILINE,
)


def _blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _result_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for block in _blocks(content):
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def read_jsonl(path, max_bytes=8_000_000):
    """Yield parsed JSON objects; reads only the tail of very large files."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            yield entry


def parse_transcript(path):
    """Flatten a transcript into events.

    Event kinds: user_text, assistant_text, tool_use, tool_result.
    """
    events = []
    for entry in read_jsonl(path):
        if entry.get("isMeta"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role") or entry.get("type")
        stamp = entry.get("timestamp", "")
        for block in _blocks(message.get("content")):
            kind = block.get("type")
            if role == "user" and kind == "text":
                text = str(block.get("text", ""))
                if text.strip() and not text.lstrip().startswith(NOISE_PREFIXES):
                    events.append({"kind": "user_text", "text": text, "ts": stamp})
            elif role == "user" and kind == "tool_result":
                events.append({
                    "kind": "tool_result",
                    "id": block.get("tool_use_id"),
                    "text": _result_text(block.get("content")),
                    "is_error": bool(block.get("is_error")),
                    "ts": stamp,
                })
            elif role == "assistant" and kind == "text":
                text = str(block.get("text", ""))
                if text.strip():
                    events.append({"kind": "assistant_text", "text": text, "ts": stamp})
            elif role == "assistant" and kind == "tool_use":
                events.append({
                    "kind": "tool_use",
                    "id": block.get("id"),
                    "name": str(block.get("name", "")),
                    "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                    "ts": stamp,
                })
    _attach_results(events)
    return events


def _attach_results(events):
    results = {e["id"]: e for e in events if e["kind"] == "tool_result" and e.get("id")}
    for event in events:
        if event["kind"] != "tool_use":
            continue
        result = results.get(event.get("id"))
        if result is None:
            event["ok"] = None
            event["result"] = ""
            continue
        text = result.get("text", "")
        failed = result.get("is_error") or bool(FAILURE_TEXT.search(text[:1500]))
        event["ok"] = not failed
        event["result"] = text


def split_turns(events):
    """Split events into turns; each turn starts at a real user message."""
    turns = []
    current = []
    for event in events:
        if event["kind"] == "user_text" and current:
            turns.append(current)
            current = []
        current.append(event)
    if current:
        turns.append(current)
    return turns


def last_assistant_text(events):
    for event in reversed(events):
        if event["kind"] == "assistant_text":
            return event["text"]
    return ""


def recent_user_text(events, count=3):
    texts = [e["text"] for e in events if e["kind"] == "user_text"]
    return " ".join(texts[-count:])


def unverified_done_problem(text, turn):
    """Return a reason string when a 'done' claim has no check behind it."""
    if not is_done_claim(text):
        return None
    tool_uses = [e for e in turn if e["kind"] == "tool_use"]
    edit_positions = [i for i, e in enumerate(tool_uses) if e["name"] in EDIT_TOOLS and e.get("ok") is not False]
    if not edit_positions:
        return None
    checks = [e for e in tool_uses[edit_positions[-1] + 1:] if e["name"] not in EDIT_TOOLS]
    if not checks:
        return (
            "Precedent check: you said the work is done, but nothing ran after your last edit. "
            "Run a real check now (tests, run the program, read the file back, query the state) "
            "and show the result. If you cannot check it, say plainly that it is unverified."
        )
    if checks[-1].get("ok") is False:
        return (
            "Precedent check: you said the work is done, but your last check failed. "
            "Read that output, fix the cause, and check again. Do not report success over a failing check."
        )
    return None


# ---------------------------------------------------------------------------
# Precedent search
# ---------------------------------------------------------------------------

STOPWORDS = set("""
a an and are as at be been before but by can cant could did didnt do does done dont earlier
for from get got had has have he her here him his how i if im in into is it its ive just
know last let like make me my no not now of on once one or our please previously really
run same see she should so still sure than that the their them then there these they
this those time to try u up us use used want was we were what when where which who why
will with work worked would yes yesterday you your youve again already able unable
keep trying give cannot thing things way something anything help also okay ok hey
""".split())

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{1,}")


def tokens(text):
    found = set()
    for raw in TOKEN.findall((text or "").lower()):
        for part in re.split(r"[_\-]", raw) + [raw]:
            if len(part) >= 3 and part not in STOPWORDS:
                found.add(part)
    return found


SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|auth)\b(\s*[=:]\s*|\s+)(\S+)"), r"\1\2***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"(?i)(--password[= ])\S+"), r"\1***"),
    (re.compile(r"://[^/\s:@]+:[^/\s@]+@"), "://***@"),
    (re.compile(r"\b(?:gh[pousr]_|sk-|xox[abp]-|AKIA)[A-Za-z0-9_\-]{8,}"), "***"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "***"),
]


def redact(text):
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def summarize_input(name, tool_input):
    for key in ("command", "file_path", "notebook_path", "url", "query", "pattern", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    try:
        return json.dumps(tool_input, ensure_ascii=False)[:300]
    except (TypeError, ValueError):
        return ""


def _one_line(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def transcript_candidates(path, skip_last_turn, same_project):
    """Successful actions from one transcript, each with the task it served."""
    events = parse_transcript(path)
    turns = split_turns(events)
    if skip_last_turn and turns:
        turns = turns[:-1]
    session = Path(path).stem[:8]
    for turn in turns:
        task = next((e["text"] for e in turn if e["kind"] == "user_text"), "")
        uses = [e for e in turn if e["kind"] == "tool_use" and e.get("ok")]
        last_index_by_tool = {}
        for index, use in enumerate(uses):
            last_index_by_tool[use["name"]] = index
        for index, use in enumerate(uses):
            if use["name"] in LOOKUP_TOOLS:
                continue
            summary = summarize_input(use["name"], use["input"])
            if not summary:
                continue
            yield {
                "source": "claude-code",
                "session": session,
                "same_project": same_project,
                "when": str(use.get("ts", ""))[:10],
                "tool": use["name"],
                "summary": summary,
                "task": task,
                "final_in_turn": last_index_by_tool[use["name"]] == index,
                "outcome": "succeeded",
            }


def shell_history_files():
    home = Path.home()
    appdata = os.environ.get("APPDATA")
    files = [home / ".bash_history", home / ".zsh_history"]
    if appdata:
        files.append(Path(appdata) / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt")
    files.append(home / ".local" / "share" / "powershell" / "PSReadLine" / "ConsoleHost_history.txt")
    return [f for f in files if f.is_file()]


def shell_candidates(max_lines=5000):
    zsh_prefix = re.compile(r"^: \d+:\d+;")
    for path in shell_history_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        except OSError:
            continue
        for line in lines:
            command = zsh_prefix.sub("", line).strip()
            if len(command) < 4:
                continue
            yield {
                "source": f"shell history ({path.name})",
                "session": "",
                "same_project": False,
                "when": "",
                "tool": "shell",
                "summary": command,
                "task": "",
                "final_in_turn": False,
                "outcome": "outcome unknown",
            }


def git_candidates(query_tokens, cwd, limit=3):
    if not query_tokens or not Path(cwd, ".git").exists():
        return
    for token in sorted(query_tokens, key=len, reverse=True)[:limit]:
        try:
            output = subprocess.run(
                ["git", "log", "--all", "-i", "-n", "5", "--date=short", "--format=%h %ad %s", f"--grep={token}"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return
        for line in output.splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3:
                yield {
                    "source": "git log",
                    "session": parts[0],
                    "same_project": True,
                    "when": parts[1],
                    "tool": "commit",
                    "summary": parts[2],
                    "task": "",
                    "final_in_turn": False,
                    "outcome": "committed",
                }


def claude_projects_dir():
    override = os.environ.get("PRECEDENT_CLAUDE_PROJECTS")
    return Path(override) if override else Path.home() / ".claude" / "projects"


def transcript_files(current_path=None, all_projects=True, max_files=60):
    """Current project's transcripts first, then other projects, newest first."""
    seen = set()
    ordered = []
    if current_path:
        project = Path(current_path).parent
        siblings = sorted(project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in siblings[:max_files]:
            ordered.append((path, True))
            seen.add(path.resolve())
    if all_projects:
        root = claude_projects_dir()
        if root.is_dir():
            others = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in others:
                if len(ordered) >= max_files:
                    break
                if path.resolve() not in seen:
                    ordered.append((path, False))
    return ordered


def score(candidate, query_tokens):
    summary_hits = len(query_tokens & tokens(candidate["summary"]))
    task_hits = len(query_tokens & tokens(candidate["task"]))
    if summary_hits == 0 and task_hits < 2:
        return 0
    value = 2 * summary_hits + task_hits
    if candidate["final_in_turn"]:
        value += 1
    if candidate["same_project"]:
        value += 1
    if candidate["outcome"] == "outcome unknown":
        value -= 1
    return value


def find_precedents(query, current_path=None, cwd=None, limit=5, include_shell=True,
                    include_git=False, all_projects=True, time_budget=4.0):
    """Rank successful past actions that match the query."""
    query_tokens = tokens(query)
    if not query_tokens:
        return []
    deadline = time.monotonic() + time_budget
    current_resolved = Path(current_path).resolve() if current_path else None
    scored = []

    for path, same_project in transcript_files(current_path, all_projects=all_projects):
        if time.monotonic() > deadline:
            break
        is_current = current_resolved is not None and path.resolve() == current_resolved
        for candidate in transcript_candidates(path, skip_last_turn=is_current, same_project=same_project):
            value = score(candidate, query_tokens)
            if value > 0:
                scored.append((value, candidate))

    if include_shell and time.monotonic() < deadline:
        for candidate in shell_candidates():
            value = score(candidate, query_tokens)
            if value > 0:
                scored.append((value, candidate))

    if include_git and cwd and time.monotonic() < deadline:
        for candidate in git_candidates(query_tokens, cwd):
            value = score(candidate, query_tokens)
            if value > 0:
                scored.append((value, candidate))

    scored.sort(key=lambda item: (item[0], item[1]["when"]), reverse=True)
    hits = []
    seen = set()
    for value, candidate in scored:
        key = (candidate["tool"], candidate["summary"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        candidate = dict(candidate, score=value, summary=redact(candidate["summary"]), task=redact(candidate["task"]))
        hits.append(candidate)
        if len(hits) >= limit:
            break
    return hits


def format_hits(hits):
    lines = []
    for hit in hits:
        where = hit["source"]
        if hit["source"] == "claude-code":
            where = "this project" if hit["same_project"] else "another project"
            where += f", session {hit['session']}"
        if hit["when"]:
            where = f"{hit['when']}, {where}"
        line = f"- [{where}; {hit['outcome']}] {hit['tool']}: `{_one_line(hit['summary'], 200)}`"
        if hit["task"]:
            line += f' (task: "{_one_line(hit["task"], 90)}")'
        lines.append(line)
    return "\n".join(lines)


def precedent_block(hits):
    if hits:
        return (
            "\n\nPossible precedent found in local history (check it matches before reusing; cite it if you use it):\n"
            + format_hits(hits)
        )
    return (
        "\n\nNo matching precedent was found in local history (Claude Code sessions, shell history). "
        "Do not claim to remember one."
    )


# ---------------------------------------------------------------------------
# Failure tracking
# ---------------------------------------------------------------------------


def tool_failure(event_name, payload):
    """Return (failed, error_text) for a PostToolUse / PostToolUseFailure payload."""
    if event_name == "PostToolUseFailure":
        error = payload.get("error") or payload.get("tool_response") or ""
        return True, error if isinstance(error, str) else json.dumps(error)[:2000]
    response = payload.get("tool_response")
    if isinstance(response, dict):
        code = response.get("exit_code", response.get("exitCode", response.get("returncode")))
        flagged = response.get("is_error") or response.get("isError") or response.get("error")
        if flagged or (isinstance(code, int) and code != 0):
            text = response.get("stderr") or response.get("error") or response.get("stdout") or ""
            return True, str(text)
        return False, ""
    if isinstance(response, str) and payload.get("tool_name") == "Bash":
        if FAILURE_TEXT.search(response[:3000]):
            return True, response
    return False, ""


def failure_signature(tool_name, error_text):
    lines = [l.strip() for l in str(error_text).splitlines() if l.strip()]
    key_line = next((l for l in reversed(lines) if re.search(r"error|exception|denied|not found|failed", l, re.I)), "")
    if not key_line and lines:
        key_line = lines[-1]
    key_line = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s'\"]+", "<path>", key_line)
    key_line = re.sub(r"0x[0-9a-fA-F]+|\d+", "#", key_line)
    return f"{tool_name}: {key_line[:160]}"


def state_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", session_id or "default")[:80]
    directory = Path(os.environ.get("PRECEDENT_STATE_DIR") or Path(tempfile.gettempdir()) / "precedent-state")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe}.json"


def load_state(session_id):
    try:
        return json.loads(state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(session_id, state):
    try:
        state_path(session_id).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Integrity checks
# ---------------------------------------------------------------------------

TEST_PATH = re.compile(
    r"(?:^|[\\/])(?:tests?|__tests__|specs?|e2e)[\\/]|(?:_test|\.test|\.spec|_spec)\.[A-Za-z0-9]+$|"
    r"(?:^|[\\/])test_[^\\/]+\.py$|(?:^|[\\/])conftest\.py$",
    re.IGNORECASE,
)
SKIP_MARKERS = re.compile(
    r"pytest\.mark\.(?:skip|xfail)|unittest\.skip|@skip\b|\b(?:it|test|describe)\.(?:skip|only)\(|"
    r"\bx(?:it|describe|test)\(|\bt\.Skip\(|#\[ignore\]|@Disabled\b|@Ignore\b"
)
TRIVIAL_ASSERTS = re.compile(
    r"\bassert\s+(?:True|1)\b|expect\(\s*true\s*\)\.toBe\(\s*true\s*\)|assertTrue\(\s*True\s*\)|assert!\(\s*true\s*\)"
)
ASSERTIONS = re.compile(r"\bassert\w*\b|\bexpect\(|\.should\b|\bassert!?\(")


def is_test_path(path):
    return bool(path) and bool(TEST_PATH.search(path))


def test_weakening_issues(old_text, new_text):
    issues = []
    if len(SKIP_MARKERS.findall(new_text)) > len(SKIP_MARKERS.findall(old_text)):
        issues.append("adds a skip/xfail/only marker")
    if len(TRIVIAL_ASSERTS.findall(new_text)) > len(TRIVIAL_ASSERTS.findall(old_text)):
        issues.append("adds an assertion that always passes")
    old_count = len(ASSERTIONS.findall(old_text))
    new_count = len(ASSERTIONS.findall(new_text))
    if old_count and new_count < old_count:
        issues.append(f"removes assertions ({old_count} -> {new_count})")
    return issues


RISKY_COMMANDS = [
    (re.compile(r"\bgit\s+push\b[^|;&]*(?:\s--force(?!-with-lease)\b|\s-f\b)"), "force-push can overwrite other people's commits"),
    (re.compile(r"--no-verify\b"), "skips git hooks and their checks"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "discards uncommitted work"),
    (re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f"), "deletes untracked files"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(?:/|~|\*|\.)(?:\s|$)"), "recursive delete of a broad path"),
    (re.compile(r"\bchmod\s+-R\s+777\b"), "opens permissions on everything"),
]


def risky_command(command):
    for pattern, why in RISKY_COMMANDS:
        if pattern.search(command or ""):
            return why
    return None


# ---------------------------------------------------------------------------
# Hook I/O helpers
# ---------------------------------------------------------------------------


def disabled():
    return os.environ.get("PRECEDENT_OFF", "").strip().lower() in {"1", "true", "yes", "on"}


def read_payload(stream):
    try:
        data = json.load(stream)
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None

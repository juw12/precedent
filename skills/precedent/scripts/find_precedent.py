#!/usr/bin/env python3
"""Find proof that a task was done before on this machine.

Searches Claude Code session transcripts (successful tool calls, with the task
they served), shell history and, optionally, git commit messages. Local only.

Usage:
    python find_precedent.py "convert xlsx to csv"
    python find_precedent.py "deploy docs" --git --limit 8
    python find_precedent.py "sftp upload" --json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import precedent_core as core  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help="words describing the task, tool, file or error")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--transcript", help="current transcript, to rank this project's sessions first")
    parser.add_argument("--git", action="store_true", help="also search git commit messages in --cwd")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--no-shell", action="store_true", help="skip shell history")
    parser.add_argument("--this-project-only", action="store_true")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    hits = core.find_precedents(
        args.query,
        current_path=args.transcript,
        cwd=args.cwd,
        limit=args.limit,
        include_shell=not args.no_shell,
        include_git=args.git,
        all_projects=not args.this_project_only,
        time_budget=15.0,
    )

    if args.json:
        print(json.dumps(hits, indent=2, ensure_ascii=False))
    elif hits:
        print("Possible precedent (check it matches before reusing):")
        print(core.format_hits(hits))
    else:
        print("No precedent found for: " + args.query)
        print("Searched: Claude Code sessions" + ("" if args.no_shell else ", shell history") + (", git log" if args.git else "") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())

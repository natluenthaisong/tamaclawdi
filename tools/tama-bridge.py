#!/usr/bin/env python3
"""
tama-bridge — drive a TamaClaude board from Claude Code *transcripts*,
with NO Claude Code hooks.

Why: on a managed Mac the `allowManagedHooksOnly` policy makes Claude Code
ignore every local hook in ~/.claude/settings.json, so the normal
`tamaclaude --hook` path never fires. This bridge gets the same information a
different, policy-legal way: it *reads* the JSONL session transcript Claude
Code writes under ~/.claude/projects/ (writing those logs is not a hook and is
not blocked) and translates each new line into the same HookEvent JSON that it
hands to `tamaclaude --send`. The running menu-bar daemon renders it on the
board exactly as if a hook had fired.

It only reads logs and sends a status to your own desk toy; it never causes
Claude Code to execute anything.

    python3 tama-bridge.py                 # follow all active sessions, live
    python3 tama-bridge.py --dry-run --from-start   # print what it WOULD send
    python3 tama-bridge.py --session <id>  # follow one session only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
DEFAULT_BIN = os.path.join(
    HOME, "Applications", "TamaClaude.app", "Contents", "MacOS", "tamaclaude"
)


def send(binary: str, payload: dict, dry: bool) -> None:
    data = json.dumps(payload, separators=(",", ":"))
    if dry:
        print("SEND", data)
        return
    try:
        subprocess.run(
            [binary, "--send", data],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(f"tama-bridge: tamaclaude binary not found at {binary}", file=sys.stderr)


def event_from_line(o: dict) -> dict | None:
    """Translate one transcript line into a HookEvent dict, or None to skip."""
    if o.get("isSidechain"):  # subagent/sidechain lines — skip in v1
        return None
    sid = o.get("sessionId")
    if not sid:
        return None
    base = {"session_id": sid, "cwd": o.get("cwd")}
    msg = o.get("message") or {}
    content = msg.get("content")
    kind = o.get("type")

    if kind == "assistant":
        tools = []
        if isinstance(content, list):
            tools = [
                b.get("name")
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
        if tools:
            # last tool_use in the message is the freshest activity
            return {**base, "hook_event_name": "PreToolUse", "tool_name": tools[-1]}
        if msg.get("stop_reason") == "end_turn":
            return {**base, "hook_event_name": "Stop"}
        return None

    if kind == "user":
        # a `user` line carrying a tool_result is a tool completion, not a prompt
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            return None
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return {**base, "hook_event_name": "UserPromptSubmit", "prompt": text[:80]}

    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Drive a TamaClaude board without hooks.")
    ap.add_argument("--binary", default=DEFAULT_BIN, help="path to the tamaclaude binary")
    ap.add_argument(
        "--active-window",
        type=float,
        default=1800,
        help="seconds; only follow transcripts modified within this window",
    )
    ap.add_argument("--poll", type=float, default=1.0, help="rescan interval, seconds")
    ap.add_argument("--dry-run", action="store_true", help="print payloads, don't send")
    ap.add_argument(
        "--from-start",
        action="store_true",
        help="replay the whole file from the top (for --dry-run testing)",
    )
    ap.add_argument("--session", help="follow only this session id (transcript stem)")
    args = ap.parse_args()

    offsets: dict[str, int] = {}  # path -> byte offset already consumed

    while True:
        paths = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
        if args.session:
            paths = [
                p
                for p in paths
                if os.path.splitext(os.path.basename(p))[0] == args.session
            ]
        else:
            now = time.time()
            paths = [p for p in paths if now - os.path.getmtime(p) <= args.active_window]

        for p in paths:
            start = offsets.get(p)
            if start is None:
                # first time we see a file: begin at its end so we don't replay
                # the whole backlog onto the board (unless explicitly asked to)
                start = 0 if args.from_start else os.path.getsize(p)
            try:
                with open(p, "r") as fh:
                    fh.seek(start)
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ev = event_from_line(o)
                        if ev:
                            send(args.binary, ev, args.dry_run)
                    offsets[p] = fh.tell()
            except FileNotFoundError:
                offsets.pop(p, None)

        if args.dry_run and args.from_start:
            break  # single pass for validation
        time.sleep(args.poll)


if __name__ == "__main__":
    main()

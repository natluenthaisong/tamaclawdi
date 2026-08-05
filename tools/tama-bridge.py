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


SUBAGENT_TOOLS = ("Task", "Agent")


def events_from_line(o: dict, pending: dict) -> list[dict]:
    """Translate one transcript line into zero or more HookEvent dicts.

    `pending` maps session_id -> set of tool_use ids for subagent (Task/Agent)
    calls we've opened but not yet seen a tool_result for. Tracking ids lets us
    emit a balanced SubagentStart/SubagentStop pair so the daemon raises its
    subagent counter and shows the `conducting` pose while a subagent runs
    (and handles several concurrent subagents correctly).
    """
    # subagent-internal (sidechain) lines never drive the parent's pose; start
    # and stop are detected from the PARENT's Task tool_use and its tool_result,
    # both of which are non-sidechain.
    if o.get("isSidechain"):
        return []
    sid = o.get("sessionId")
    if not sid:
        return []
    base = {"session_id": sid, "cwd": o.get("cwd")}
    msg = o.get("message") or {}
    content = msg.get("content")
    kind = o.get("type")
    out: list[dict] = []

    if kind == "assistant":
        tool_uses = (
            [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            if isinstance(content, list)
            else []
        )
        # a Task/Agent tool_use spawns a subagent -> SubagentStart
        opened = pending.setdefault(sid, set())
        for b in tool_uses:
            if b.get("name") in SUBAGENT_TOOLS:
                tid = b.get("id")
                if tid and tid not in opened:
                    opened.add(tid)
                    out.append({**base, "hook_event_name": "SubagentStart"})
        # pose comes from the freshest *non-subagent* tool; while a subagent is
        # open the daemon overrides whatever this is with `conducting` anyway
        plain = [b for b in tool_uses if b.get("name") not in SUBAGENT_TOOLS]
        if plain:
            out.append(
                {**base, "hook_event_name": "PreToolUse", "tool_name": plain[-1].get("name")}
            )
        elif not tool_uses and msg.get("stop_reason") == "end_turn":
            out.append({**base, "hook_event_name": "Stop"})
        return out

    if kind == "user":
        blocks = content if isinstance(content, list) else []
        # a tool_result closing a Task/Agent call -> SubagentStop
        opened = pending.get(sid)
        had_tool_result = False
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                had_tool_result = True
                tid = b.get("tool_use_id")
                if opened and tid in opened:
                    opened.discard(tid)
                    out.append({**base, "hook_event_name": "SubagentStop"})
        if had_tool_result:
            return out  # a tool completion, not a user prompt

        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "")
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            )
        out.append({**base, "hook_event_name": "UserPromptSubmit", "prompt": text[:80]})
        return out

    return out


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
    pending: dict[str, set] = {}  # session_id -> open subagent tool_use ids

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
                        for ev in events_from_line(o, pending):
                            send(args.binary, ev, args.dry_run)
                    offsets[p] = fh.tell()
            except FileNotFoundError:
                offsets.pop(p, None)

        if args.dry_run and args.from_start:
            break  # single pass for validation
        time.sleep(args.poll)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""PreToolUse hook for Read. Warns on a repeat read of the same file within one transcript
and on a whole-file read of a large file. Never blocks and adds no permission decision.
Fail-open. See _config.py for the settings."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _config as cfg  # noqa: E402


def main():
    d = json.load(sys.stdin)
    if cfg.already_seen(d):
        return
    ti = d.get("tool_input") or {}
    path = str(ti.get("file_path") or "")
    if not path:
        return
    ranged = ti.get("offset") is not None or ti.get("limit") is not None
    key = os.path.basename(d.get("transcript_path") or "") or d.get("session_id") or "nosession"
    state_dir = cfg.state_dir()
    sf = os.path.join(state_dir, key.replace("/", "_") + ".reads")
    prior, last = 0, None
    try:
        with open(sf) as f:
            for line in f:
                p, _, ts = line.rstrip("\n").rpartition("\t")
                if p == path:
                    prior += 1
                    last = float(ts)
    except (FileNotFoundError, ValueError):
        pass
    with open(sf, "a") as f:
        f.write(f"{path}\t{time.time()}\n")
    if prior == 1:
        cfg.prune_state(state_dir)
    msgs = []
    name = os.path.basename(path)
    if prior and not ranged:
        ago = int((time.time() - last) / 60) if last else 0
        msgs.append(f"Token policy: {name} was already read {prior}x in this transcript (last {ago} min ago). "
                    "Re-read only if it changed since, and then only the changed range with offset/limit; "
                    "otherwise use what is already in context.")
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if not ranged and prior == 0 and size > cfg.large_file_bytes():
        msgs.append(f"Token policy: {name} is ~{size // 1000} KB (~{size // 4000}k tokens). "
                    "Unless the task needs all of it, grep first and read ranges with offset/limit.")
    if msgs:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": " ".join(msgs)}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

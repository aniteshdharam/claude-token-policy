#!/usr/bin/env python3
"""SessionStart hook: re-inject the pre-compaction checkpoint into the fresh context.

The other half of precompact_checkpoint.py. SessionStart fires with source in
{startup, resume, clear, compact}; this hook acts only on "compact", the moment the harness
has just thrown away most of the working state. The injected text starts with
"compaction N of this session" (N from the per-session counter), and from
TOKEN_POLICY_MAX_COMPACTIONS (default 4) it adds one line suggesting a handoff note and a
fresh session at the next natural break. Warning only; nothing is blocked.

Fails open: every non-obvious exit says why on stderr.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _config as cfg  # noqa: E402

MAX_AGE_DAYS = 7      # prune older checkpoints on the way past
MAX_INJECT = 14_000   # chars; the checkpoint must stay far cheaper than what it saves


def prune(directory):
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        for name in os.listdir(directory):
            p = os.path.join(directory, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except Exception:
        pass


def read_count(directory, sid):
    try:
        with open(os.path.join(directory, f"{sid}.count")) as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def main():
    d = json.load(sys.stdin)
    if d.get("source") != "compact":
        return  # startup, resume and clear carry no lost state: stay silent and cheap

    sid = str(d.get("session_id") or "unknown").replace("/", "_")
    directory = cfg.checkpoint_dir()
    n = read_count(directory, sid)
    header = []
    if n:
        header.append(f"Compaction {n} of this session.")
        if n >= cfg.max_compactions():
            header.append(
                f"This session has compacted {n} times and detail loss compounds with each one. At the next "
                "natural break (a task finished, a pull request opened), write a handoff note and continue in "
                "a fresh session from it. Do not stop in the middle of a task for this.")

    path = os.path.join(directory, f"{sid}.md")
    body = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            body = f.read()
        if len(body) > MAX_INJECT:
            body = body[:MAX_INJECT] + "\n\n_(checkpoint truncated at injection)_\n"
    else:
        print(f"session_start_checkpoint: compaction detected for session {sid} but no checkpoint at "
              f"{path}; the PreCompact hook did not run or failed. This session resumes without its "
              "pre-compaction state.", file=sys.stderr)

    text = "\n".join(header) + ("\n\n" if header and body else "") + body
    if not text.strip():
        return
    json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}, sys.stdout)
    prune(directory)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"session_start_checkpoint: FAILED ({e}); no checkpoint injected.", file=sys.stderr)
    sys.exit(0)

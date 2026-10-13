#!/usr/bin/env python3
"""UserPromptSubmit / PostToolUse hook. Warns when a prompt lands on an expired prompt cache
(idle longer than the idle gap, above the idle-resume context size) and when context nears
the compaction trigger. Informational only. Fail-open.

The checkpoint line is DERIVED from the configured autoCompactWindow, never a fixed number in code.
The harness compacts when the next turn is projected to exceed the window, so the real
trigger sits near 90% of it; a fixed number copied from the setting can end up above the
trigger and never fire. Both lines here are fractions of the window, so they stay in
proportion whenever the window moves.

A latch makes this safe to run from PostToolUse (which fires on every tool call): once the
checkpoint warning has fired it stays quiet until the PreCompact hook clears the latch.
See _config.py for the settings."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _config as cfg  # noqa: E402


def main():
    d = json.load(sys.stdin)
    event = d.get("hook_event_name") or "UserPromptSubmit"
    sid = d.get("session_id") or "unknown"
    latch = cfg.latch_path(sid)
    off_latch = cfg.off_latch_path(sid)

    # PostToolUse runs on every tool call; once the warnings have fired for this cycle, exit on two
    # stat() calls. Otherwise the settings files are read, which is small next to the transcript tail.
    if event == "PostToolUse" and os.path.exists(latch) and os.path.exists(off_latch):
        return
    off = cfg.autocompact_off()
    if event == "PostToolUse" and os.path.exists(latch) and not off:
        return
    if cfg.already_seen(d):
        return
    window = cfg.compaction_window()
    msgs = []
    # Once per session, at any context size: with compaction off, a small-window session hits
    # the hard limit before any checkpoint line, and a large one re-sends ever more context.
    if off and not os.path.exists(off_latch):
        try:
            open(off_latch, "w").close()
        except Exception:
            pass
        msgs.append(
            f"Token policy: automatic compaction is SWITCHED OFF ({off}), so this session will not "
            f"compact at the {window // 1000}k window; context grows to the model's limit and every call "
            "re-sends all of it. Tell the user in one line that compaction is off and where it is set; do "
            "not change the setting yourself. Keep a notes or handoff file current, then continue the work.")
    _, ctx, ts = cfg.tail_usage(d.get("transcript_path") or "", main_only=True)
    if not ctx and not msgs:
        return
    trigger = int(window * cfg.TRIGGER_FRACTION)
    checkpoint = int(window * cfg.CHECKPOINT_FRACTION)
    gap = cfg.minutes_since(ts) if ts else None
    k = ctx // 1000
    if gap is not None and gap > cfg.idle_minutes() and ctx > cfg.idle_resume_tokens():
        msgs.append(
            f"Token policy: this session sat idle {int(gap)} min at ~{k}k context, so its prompt cache has "
            "expired and this turn re-writes the whole context at the cache-write price. If the previous task "
            "is complete, keep this turn short or continue in a fresh session; if the work continues here, proceed.")
    if ctx > checkpoint and not os.path.exists(latch):
        try:
            os.makedirs(cfg.state_dir(), exist_ok=True)
            open(latch, "w").close()
        except Exception:
            pass
        if off:
            msgs.append(
                f"Token policy: context is ~{k}k, past the {checkpoint // 1000}k checkpoint line, and automatic "
                "compaction is off, so no compaction is coming. Write anything not yet in a notes or handoff "
                "file now, then continue the work.")
        else:
            msgs.append(
                f"Token policy: context is ~{k}k, past the {checkpoint // 1000}k checkpoint line; this session "
                f"compacts at about {trigger // 1000}k (90% of the {window // 1000}k window), so roughly "
                f"{max(trigger - ctx, 0) // 1000}k of runway is left. A PreCompact hook will checkpoint the brief, "
                "the recent narrative and the files already in hand, but it cannot capture a decision you have "
                "only reasoned about. Write those into a notes or handoff file now, then continue the work.")
    if msgs:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                                 "additionalContext": " ".join(msgs)}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

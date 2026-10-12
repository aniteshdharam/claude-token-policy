#!/usr/bin/env python3
"""PreToolUse hook for Agent/Task dispatches.

Refuses a subagent on the top tier, refuses a fork from a session running on the top tier,
and refuses a fork when the parent context is above the fork limit. Sets Explore to a
fast model and gives an unnamed generic dispatch a default model. With TOKEN_POLICY_MODE=warn
every refusal becomes a warning. Fail-open: any error means no output and exit 0.
See _config.py for the settings."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _config as cfg  # noqa: E402

GENERIC = {"general-purpose", "general", "Explore", "Plan", "claude", "sonnet", "opus", "haiku", "fable"}


def out(**hso):
    hso["hookEventName"] = "PreToolUse"
    print(json.dumps({"hookSpecificOutput": hso}))
    sys.exit(0)


def refuse(reason):
    if cfg.warn_only():
        out(additionalContext="Token policy (warn mode, not blocked): " + reason)
    out(permissionDecision="deny", permissionDecisionReason="Token policy: " + reason)


def main():
    d = json.load(sys.stdin)
    if cfg.already_seen(d):
        return
    ti = d.get("tool_input") or {}
    if not isinstance(ti, dict):
        return
    styp = ti.get("subagent_type") or ti.get("agent_type") or "general-purpose"
    model = str(ti.get("model") or "").lower()
    pmodel, ctx, _ = cfg.tail_usage(d.get("transcript_path") or "")
    top = cfg.top_tier()
    parent_top = bool(top) and top in pmodel.lower()
    k = ctx // 1000

    if styp == "fork":
        if parent_top:
            refuse(f"this session runs on the top tier ({top}), and a fork runs on the parent's model with the "
                   f"parent's whole context (~{k}k tokens) re-sent on every turn. Dispatch a fresh subagent "
                   "instead: the strong tier for judgement work, the fast tier for mechanical work, with a written "
                   "brief (the delta, the files, the question).")
        limit = cfg.fork_max_tokens()
        if ctx > limit:
            refuse(f"this fork would carry ~{k}k tokens of this session's context on every turn (limit "
                   f"{limit // 1000}k). Dispatch a fresh subagent with a written brief (the delta, the files, "
                   "the question) and name its model.")
        out(additionalContext=f"Token policy: fork allowed at ~{k}k parent context; it re-sends that context "
                              "every turn, so keep it short-lived.")

    if top and top in model:
        refuse(f"the top tier ({top}) is not a subagent tier. Pass the strong-tier model for judgement work "
               "(review, design, debugging), the fast-tier model for mechanical work (search, census, "
               "sweeps, formatting, bulk transforms) or ordinary building (with a written brief), and keep "
               "top-tier decisions in the main session.")

    explore = cfg.str_env("TOKEN_POLICY_EXPLORE_MODEL", "sonnet")
    default = cfg.str_env("TOKEN_POLICY_DEFAULT_MODEL", "opus")
    if not model and styp == "Explore" and explore:
        out(updatedInput={**ti, "model": explore},
            additionalContext=f"Token policy: Explore only searches (mechanical work); model set to {explore}.")
    if not model and styp in GENERIC and default:
        out(updatedInput={**ti, "model": default},
            additionalContext=(f"Token policy: no model named; defaulted to {default}. Name the tier yourself: "
                               "the fast tier for mechanical work (search, census, sweeps, formatting) and for ordinary "
                               "building with a written brief; the strong tier for judgement (review, design, debugging)."))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)

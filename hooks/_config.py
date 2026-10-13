"""Shared configuration and helpers for the token-policy hooks. Standard library only.

Every knob is an environment variable; set it in the `env` block of settings.json or in
your shell profile. Unset or unparsable values fall back to the default.

  TOKEN_POLICY_TOP_TIER          default "fable"
      Substring of the model name for the most expensive tier. A model whose name contains
      it is never allowed as a subagent, and a fork from a session running on it is refused.
      Set to an empty string to switch the tier rules off.
  TOKEN_POLICY_FORK_MAX_TOKENS   default 100000
      A fork re-sends the parent's whole context on every turn. Forks are refused when the
      parent context is above this many tokens.
  TOKEN_POLICY_IDLE_RESUME_TOKENS default 150000
      A prompt that lands after the idle gap on a context larger than this gets a warning.
  TOKEN_POLICY_IDLE_MINUTES      default 60
      Idle gap (minutes) after which the prompt cache is assumed expired.
  TOKEN_POLICY_LARGE_FILE_BYTES  default 60000
      A whole-file Read of a file larger than this gets a "grep first" warning.
  TOKEN_POLICY_MODE              default "enforce"
      "enforce": refusals refuse. "warn": every refusal becomes a warning instead.

  TOKEN_POLICY_MAX_COMPACTIONS   default 4
      From this many compactions in one session, the re-injected checkpoint carries one
      extra line suggesting a handoff note and a fresh session at the next natural break.
      Warning only; nothing is ever blocked.

Optional extras for the dispatch hook:
  TOKEN_POLICY_EXPLORE_MODEL     default "sonnet"  model set on an Explore dispatch with none named
  TOKEN_POLICY_DEFAULT_MODEL     default "opus"    model set on other generic dispatches with none named
                                                   (set to an empty string to leave them alone)

Compaction window: the checkpoint warning line is derived as a fraction of the configured
`autoCompactWindow` (managed settings first, then project, then user), never a fixed number in code.
If automatic compaction is switched off (`autoCompactEnabled: false`, `DISABLE_COMPACT` or
`DISABLE_AUTO_COMPACT`), the turn guard says so once per session instead of promising runway.
"""
import datetime
import hashlib
import json
import os
import time

DEFAULT_WINDOW = 350_000
TRIGGER_FRACTION = 0.90     # the harness compacts at about 90% of the configured window
CHECKPOINT_FRACTION = 0.77  # warn at 77% of the window: about 13% of runway before compaction


def int_env(name, default):
    try:
        v = int(float(os.environ.get(name, "").strip()))
        return v if v >= 0 else default
    except Exception:
        return default


def str_env(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip()


def top_tier():
    return str_env("TOKEN_POLICY_TOP_TIER", "fable").lower()


def fork_max_tokens():
    return int_env("TOKEN_POLICY_FORK_MAX_TOKENS", 100_000)


def idle_resume_tokens():
    return int_env("TOKEN_POLICY_IDLE_RESUME_TOKENS", 150_000)


def idle_minutes():
    return int_env("TOKEN_POLICY_IDLE_MINUTES", 60)


def large_file_bytes():
    return int_env("TOKEN_POLICY_LARGE_FILE_BYTES", 60_000)


def max_compactions():
    return int_env("TOKEN_POLICY_MAX_COMPACTIONS", 4)


def warn_only():
    return str_env("TOKEN_POLICY_MODE", "enforce").lower() == "warn"


def state_dir():
    """Per-user hook state, never a shared temp folder: another local user cannot plant or
    pre-create files here. Created owner-only (0700)."""
    d = os.path.expanduser("~/.claude/token-policy-state")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def checkpoint_dir():
    return os.path.expanduser("~/.claude/checkpoints")


def latch_path(session_id):
    return os.path.join(state_dir(), "ctx-warned-" + str(session_id))


STATE_MAX_AGE_DAYS = 3


def prune_state(d=None, max_age_days=STATE_MAX_AGE_DAYS):
    """Delete state files older than max_age_days. Every hook event leaves one small marker
    here, so without pruning the folder grows by one file per tool call for ever."""
    try:
        d = d or state_dir()
        cut = time.time() - max_age_days * 86400
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if os.path.getmtime(p) < cut:
                os.remove(p)
    except Exception:
        pass


def already_seen(d):
    """True if this exact hook event was already handled (the hook can be registered twice,
    for example once by a plugin and once by a user settings file). Marks the event as seen.
    One event in sixteen also prunes old markers, so the state folder stays small."""
    try:
        raw = (str(d.get("tool_use_id") or "") + str(d.get("session_id") or "")
               + str(d.get("prompt") or "") + json.dumps(d.get("tool_input") or {}, sort_keys=True))
        key = hashlib.sha1(raw.encode()).hexdigest()[:24]
        mark = os.path.join(state_dir(), "seen-" + key)
        if os.path.exists(mark):
            return True
        open(mark, "w").close()
        if key[0] == "0":
            prune_state()
    except Exception:
        pass
    return False


def tail_usage(path, main_only=False):
    """(model, context tokens, timestamp) of the last model call in a transcript, or ('', 0, None)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 400_000))
            data = f.read().decode("utf-8", "ignore")
        for line in reversed(data.splitlines()):
            if '"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "assistant" or (main_only and d.get("isSidechain")):
                continue
            m = d.get("message") or {}
            u = m.get("usage") or {}
            if not u:
                continue
            ctx = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                   + (u.get("cache_creation_input_tokens") or 0))
            return (m.get("model") or ""), ctx, d.get("timestamp")
    except Exception:
        pass
    return "", 0, None


def minutes_since(ts):
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() / 60
    except Exception:
        return None


def _parse_window(v):
    """autoCompactWindow is written either as an int (350000) or a string ("350k")."""
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        t = v.strip().lower().replace(",", "")
        try:
            return int(float(t[:-1]) * 1000) if t.endswith("k") else int(float(t))
        except Exception:
            return None
    return None


MANAGED_SETTINGS = ("/Library/Application Support/ClaudeCode/managed-settings.json",
                    "/etc/claude-code/managed-settings.json")


def settings_paths():
    """Settings files, highest precedence first: managed, project local, project, user local, user."""
    paths = list(MANAGED_SETTINGS)
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj:
        paths += [os.path.join(proj, ".claude", "settings.local.json"),
                  os.path.join(proj, ".claude", "settings.json")]
    paths += [os.path.expanduser("~/.claude/settings.local.json"),
              os.path.expanduser("~/.claude/settings.json")]
    return paths


def compaction_window():
    """The configured autoCompactWindow, from the highest-precedence settings file that sets it."""
    for p in settings_paths():
        try:
            with open(p) as f:
                w = _parse_window(json.load(f).get("autoCompactWindow"))
            if w:
                return w
        except Exception:
            continue
    return DEFAULT_WINDOW


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def autocompact_off():
    """Why automatic compaction is off, or None when it is on.

    Claude Code turns automatic compaction off when DISABLE_COMPACT or DISABLE_AUTO_COMPACT is
    set in the environment, and otherwise follows the autoCompactEnabled setting (default on).
    With it off, autoCompactWindow does nothing: context grows to the model's hard limit and
    every call re-sends all of it. The /config menu's Auto-compact toggle writes the setting
    without a trace in any transcript, so the turn guard says so out loud."""
    for var in ("DISABLE_COMPACT", "DISABLE_AUTO_COMPACT"):
        if _truthy(os.environ.get(var)):
            return "%s=%s in the environment" % (var, os.environ[var])
    for p in settings_paths():
        try:
            with open(p) as f:
                s = json.load(f)
        except Exception:
            continue
        if isinstance(s, dict) and "autoCompactEnabled" in s:
            return '"autoCompactEnabled": false in %s' % p if s["autoCompactEnabled"] is False else None
    return None


def off_latch_path(session_id):
    return os.path.join(state_dir(), "compact-off-warned-" + str(session_id))

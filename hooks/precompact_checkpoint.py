#!/usr/bin/env python3
"""PreCompact hook: write a state checkpoint immediately before the harness compacts.

Why this exists
---------------
Auto-compaction drops a session from several hundred thousand tokens to a short summary and
keeps only a handful of raw messages. In the tool calls that follow, a large share of file
reads (about half, in the author's measurements) re-touch a path the session had already read,
so the session rebuilds by hand what it just lost. This hook does not depend on a threshold
being crossed or on the model noticing anything: it fires when compaction is happening.

It writes ~/.claude/checkpoints/<session>.md (the brief the session started with, the most
recent instruction, task state, recent narrative, files already in hand, recent commands) and
bumps a per-session compaction counter in ~/.claude/checkpoints/<session>.count. The
session_start_checkpoint.py hook reads both back after the compaction.

MUST FAIL OPEN. A PreCompact hook can block compaction, and a blocked compaction lets the
session run past its window, which is worse than the problem this fixes. Every path here
exits 0 and emits no decision. Nothing is written to stdout.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _config as cfg  # noqa: E402

MAX_NARRATIVE = 8       # recent assistant text blocks to carry
MAX_TEXT = 700          # chars per block
MAX_PATHS = 30          # recently touched files
MAX_CMDS = 12           # recent shell commands
TAIL_BYTES = 2_000_000  # transcript tail to scan

PATH_RE = re.compile(r"(/[\w./\-]+\.(?:py|md|json|ya?ml|sh|txt|tf|toml|sql|ts|tsx|js))")

# A "user" message in the transcript is not necessarily something a person typed: task
# notifications, system reminders, hook output and command stdout all arrive with role=user.
# Carrying one of those forward as "the instruction this session is working to" is worse than
# carrying nothing, so only text that starts with none of these counts as an instruction.
SYNTHETIC = (
    "<task-notification>", "<system-reminder>", "<local-command-",
    "<command-",
    "[Request interrupted", "Caveat:", "<user-prompt-submit-hook>",
)


# Secrets typed into a prompt or a shell command would otherwise be copied into the checkpoint.
# Best effort: blank the value of anything shaped like a key, token or password. The file is
# also written owner-only (0600) in an owner-only folder (0700).
REDACT = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                r"|AKIA[0-9A-Z]{16}|xox[abposr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})"), "[REDACTED]"),
    (re.compile(r"(://[^/\s:@]+:)[^@\s/]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
                r"[A-Za-z0-9_]*)(\s*[=:]\s*|\s+--?\S*=)(['\"]?)[^\s'\"]+\3"), r"\1\2\3[REDACTED]\3"),
]


def redact(text):
    for pat, rep in REDACT:
        text = pat.sub(rep, text)
    return text


def write_private(path, body):
    """Atomic, owner-only (0600) write: SessionStart never reads a half-written file."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, path)


def is_real_instruction(text):
    return bool(text) and not text.lstrip().startswith(SYNTHETIC)


def read_tail(path):
    """Last TAIL_BYTES of the transcript, as whole lines."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - TAIL_BYTES))
        data = f.read().decode("utf-8", "ignore")
    lines = data.splitlines()
    return lines[1:] if size > TAIL_BYTES and lines else lines


def _texts(content):
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return []


def first_instruction(path, max_lines=4000):
    """The brief this session started with, scanned from the head of the transcript: after a
    second compaction the original brief is far outside any tail window, and it is the most
    valuable thing to carry forward. A compaction summary is not an instruction."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i > max_lines:
                    break
                if '"role":"user"' not in line or '"promptId"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("isCompactSummary") or d.get("isSidechain"):
                    continue
                for t in _texts((d.get("message") or {}).get("content")):
                    if is_real_instruction(t) and len(t.strip()) > 40:
                        return t[:2500]
    except Exception:
        pass
    return None


def harvest(lines):
    narrative, paths, cmds, todos = [], [], [], None
    last_user = None
    for line in lines:
        if '"message"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("isSidechain"):
            continue
        msg = d.get("message") or {}
        content = msg.get("content")
        if msg.get("role") == "user" and d.get("promptId") and not d.get("isCompactSummary"):
            for t in _texts(content):
                if is_real_instruction(t):
                    last_user = t[:1200]
        if d.get("type") != "assistant" or not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                t = (b.get("text") or "").strip()
                if t:
                    narrative.append(t[:MAX_TEXT])
            elif b.get("type") == "tool_use":
                name, inp = b.get("name"), b.get("input") or {}
                if name == "TodoWrite":
                    todos = inp.get("todos")
                for p in PATH_RE.findall(json.dumps(inp)):
                    if p not in paths:
                        paths.append(p)
                if name == "Bash":
                    c = (inp.get("command") or "").strip().replace("\n", " ")
                    if c:
                        cmds.append(c[:200])
    return narrative, paths, cmds, todos, last_user


def render(d, brief, narrative, paths, cmds, todos, last_user):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "# Pre-compaction checkpoint",
        "",
        f"Written {now} by the PreCompact hook, immediately before the harness "
        f"compacted this session (trigger: {d.get('trigger', 'unknown')}).",
        "",
        "The compaction summary that follows is lossy. Everything below survived because this hook",
        "copied it out first. Read the 'Files already in hand' list before re-reading anything.",
        "",
        f"- session: `{d.get('session_id', '?')}`",
        f"- cwd: `{d.get('cwd', '?')}`",
    ]
    if brief:
        out += ["", "## The brief this session started with", "", "```", brief.strip(), "```"]
    if last_user and last_user.strip() != (brief or "").strip():
        out += ["", "## Most recent instruction", "", "```", last_user.strip(), "```"]
    if todos:
        out += ["", "## Task state at compaction"]
        for t in todos:
            if isinstance(t, dict):
                mark = {"completed": "x", "in_progress": ">"}.get(t.get("status"), " ")
                out.append(f"- [{mark}] {t.get('content') or t.get('activeForm') or ''}")
    if narrative:
        out += ["", "## What the session was saying (most recent last)", ""]
        out += [f"{i}. {t}" for i, t in enumerate(narrative[-MAX_NARRATIVE:], 1)]
    if paths:
        out += ["", "## Files already in hand (read or edited before compaction, do not blindly re-read)", ""]
        out += [f"- `{p}`" for p in paths[-MAX_PATHS:]]
    if cmds:
        out += ["", "## Recent commands", "", "```"]
        out += cmds[-MAX_CMDS:]
        out += ["```"]
    return "\n".join(out) + "\n"


def bump_counter(directory, sid):
    """Increment <session>.count (atomic replace). Returns the new count."""
    path = os.path.join(directory, f"{sid}.count")
    n = 0
    try:
        with open(path) as f:
            n = int(f.read().strip() or 0)
    except Exception:
        n = 0
    n += 1
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(n))
    os.replace(tmp, path)
    return n


def main():
    d = json.load(sys.stdin)
    sid = str(d.get("session_id") or "unknown").replace("/", "_")
    directory = cfg.checkpoint_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    # Count first: the counter must advance even if the transcript is missing or unreadable.
    try:
        bump_counter(directory, sid)
    except Exception:
        pass
    tp = d.get("transcript_path")
    if tp and os.path.exists(tp):
        body = redact(render(d, first_instruction(tp), *harvest(read_tail(tp))))
        write_private(os.path.join(directory, f"{sid}.md"), body)
    # Re-arm the context warning: compaction resets context, so the next climb deserves a new one.
    try:
        latch = cfg.latch_path(sid)
        if os.path.exists(latch):
            os.remove(latch)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)  # never block compaction

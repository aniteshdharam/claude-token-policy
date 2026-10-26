---
name: token-policy
description: Use when starting or running a long Claude Code session, dispatching any subagent, choosing a model tier or effort, deciding whether to fork, read a large file, compact, resume an idle session, or hand off to a fresh one, and when setting up a review. Cuts token spend without moving quality.
---

# Token policy

The bill is **context size × number of calls**, not the model tier. Every call re-reads the
whole session. Keep the context small and the calls purposeful; name the tier on every
dispatch.

## Before any action, check

| You are about to | Do this |
|---|---|
| Dispatch a subagent | Name its tier. Judgement (review, design, verdict) → `opus`. Mechanical (search, sweep, format, bulk edit) → `sonnet` (`haiku` for yes/no checks). Ordinary building → the `builder` agent with a written brief. Never the top tier as a subagent. |
| Fork the session | Only below ~100k context, never from the top tier. Otherwise dispatch a fresh agent with a written brief. |
| Run a review | A fresh agent on the delta, never a fork. Size it to the risk: ordinary work → the `reviewer` agent (strong tier, high effort); a critical surface → a general `opus` agent, which runs at the effort your settings give `opus` (keep it at your top everyday level, `xhigh`). Re-review only the delta since the last reviewed commit. |
| Read a file | Read it once. After that, search it and read the changed range (after a compaction, the range the next step needs). Over ~60 KB: search first, then read ranges. |
| Brief a subagent | State the delta, the files, the question, the tier and the size of the answer you want back. Ask for a bounded summary, never a transcript. |
| Leave a big session idle | Don't. Above ~150k, an hour idle loses the cache and the next prompt re-writes everything. Finish, write the hand-off, end. |
| Switch model mid-session | Don't. It re-writes the cache. Pick the tier at the start. |
| Write a conclusion into a hand-off, journal or memory | Check it first (a test, a command, the quoted line) and say how; otherwise mark it unchecked. Your own notes come back reading as fact. |
| Correct a note that turned out wrong | Rewrite or delete the old note; never leave both versions for the next reader. |
| Start a session or pick an agent | Switch off MCP servers the session will not use (`/mcp`); dispatch an agent whose file lists only the tools its job needs. |
| Pass the fourth compaction | At the next natural break (task done, PR opened), write a hand-off note and continue in a fresh session from it. Never stop mid-task for this. |

## Who builds

**Critical** (the strong-tier main session builds it itself, never dispatched): database
migrations, deletes and overwrites of stored data; infrastructure, deploy and CI config; any
command run against production; auth, secrets, and code that moves personal data; what the
product serves publicly; test expected values and the gates that read them; paid API spend.
In doubt, it is critical.

**Ordinary** (the `builder` agent, fast tier, high effort): everything else. Lock the design
in the brief first. The builder never reviews its own work, never pushes, and hands back the
moment it meets a critical surface. Escalation is one-way.

## Working habits

- Start from the boot prompt or hand-off; do not re-read what it summarised.
- Keep the journal or hand-off file current, so a compaction loses nothing that matters.
- Prefer a script or a search over a model reading files aloud.
- Do not run a full local test suite that CI repeats identically.
- Close with the outcome first; long write-ups go to a file, not the chat.

## When a hook speaks

The plugin's hooks warn or refuse with a reason. Read the reason, adapt, and carry on; they
never need a human at the keyboard. To measure the effect, run `/token-census`.

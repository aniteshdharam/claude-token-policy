# The token policy

A working policy for teams that run Claude Code hard: many sessions a day, long-running
sessions, subagents, reviews. It is written so that quality does not move. What it cuts is
context that was re-read for nothing, model tiers inherited by accident, and ceremony the work
did not need.

## The finding

**The bill is context size × number of calls, not the model tier.**

Every model call re-reads the whole session so far. Prompt caching makes that re-read cheap
per token, but not free, and it happens on every call. A session at 800k context pays roughly
four times what a session at 200k pays for the same call, on every call. In our measurement,
97.6% of all tokens processed were re-reads of context already in the session; prompts, typed
or not, were 0.02%.

The second bill is the tier a subagent inherits when nobody names one: 70% of our dispatches
named no model, and they quietly ran on whatever the parent ran, often the most expensive tier.

## The four rules (in order of money)

1. **Context is the bill. Work inside 200k.** Use the standard window by default. A 1M window
   is opt-in per session, for a stated reason (one document or dataset that cannot be split).
   Let compaction happen automatically, and keep a journal or hand-off file current so a
   compaction loses nothing that matters.
2. **Do not park a big session and come back.** The prompt cache expires after a period of
   idleness. A session above ~150k that sits idle for an hour pays to re-write its whole
   context at the cache-write price on the next prompt. End a session when its task ends; the
   hand-off file is the memory, not the window.
3. **Every dispatch names its tier.** Judgement (review, design, verdicts) gets the strong
   tier. Mechanical work (search, sweeps, formatting, bulk edits) gets the fast tier. Ordinary
   building gets the fast tier at high effort with a written brief. The top tier is for the
   human's own spec and advice sessions and for one verdict per review round on a critical
   task. It is never a subagent tier, never forked, never the bulk of a review.
4. **Read once.** Read a file once per session; after that, search it and read the changed
   range. Subagents return a summary sized to the question, never a transcript. Large files
   are read in ranges after a search unless the task is to edit them throughout.

## Long-running sessions

Keeping one session running from task to task saves the cost of booting a new one, but each
compaction is a lossy summary, and the loss compounds. **After the fourth compaction, replace
the session** at the next natural break (a task finished, a pull request opened): write the
hand-off note, start a fresh session from it. Never stop mid-task to do it.

We added this rule after the measurement below found sessions running 70 and 104 compactions.
Each call in them was cheap; the sessions simply never ended.

## Who builds

Split the work by how costly a wrong line is.

- **Critical** means a wrong line is hard to undo, takes effect on stored or served data
  without a further human step, spends money, or exposes a person's data or a secret:
  database migrations and deletes, infrastructure and deploy config, production commands,
  auth and secrets, what your product serves publicly, the test oracle, paid API spend. The
  strong-tier main session builds these itself.
- **Everything else** is ordinary building. It goes to a fast-tier build agent with a written
  brief. The design is locked in the brief before it starts; it builds, it never reviews its
  own work, and it stops and hands back the moment it meets a critical surface.
- Escalation is one-way: a slice never moves down a tier mid-flight.

## Review

- Price review to risk. A mechanical change gets a light review; a risky change gets an
  adversarial panel (several independent finders, then a verifier per finding who may only
  dismiss it by quoting the line that makes it impossible). Do not buy the panel "to be safe".
- Review runs as a **fresh agent on the delta**, never as a fork of the build session (a fork
  re-sends the build session's whole context on every turn).
- Re-review only the delta since the last reviewed commit, never the whole change again.

## Session hygiene

- Start from the boot prompt or hand-off; do not re-read what it already summarised.
- Pick the model at the start. Switching mid-session re-writes the cache.
- Load a skill when its work is actually being done, not because a prompt mentions it.
- Prefer deterministic tools over model reading where both do the job: a script over
  transcripts, a search over a corpus.
- Do not run a full local test suite that CI will repeat identically.
- Close-outs carry substance, not narration: lead with the outcome.

## Enforce it by machine

A policy that relies on memory decays. Ours held because the defaults and hooks do the work:

| Rule | Mechanism |
|---|---|
| Standard window, strong tier by default | `"model": "opus"` in settings (the family name, so upgrades need no edit) |
| Compaction before context gets huge | `autoCompactWindow`, with `autoCompactEnabled` left on (the turn guard warns if it is off); note compaction fires at roughly 90% of the configured number |
| Unnamed dispatches never inherit the top tier | `CLAUDE_CODE_SUBAGENT_MODEL=opus` |
| No top-tier subagents, no forks from the top tier, no forks above 100k | `dispatch_guard` hook |
| Re-read and whole-large-file warnings | `read_guard` hook (warns, never blocks) |
| Idle-resume and context-size warnings | `turn_guard` hook |
| Compaction keeps working state; replace after the fourth | `precompact_checkpoint` + `session_start_checkpoint` hooks |
| Context shown in absolute tokens | status line |

## Re-measure

Run `/token-census` (or `python3 scripts/token_census.py --since YYYY-MM-DD`) monthly and
after any change to tiers or defaults. Keep your own before/after numbers; the policy is only
as good as the last measurement says it is.

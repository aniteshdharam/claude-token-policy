---
description: Print a token census of your local Claude Code sessions and summarise it
allowed-tools: Bash(python3:*)
argument-hint: "[YYYY-MM-DD start date, default 21 days ago]"
---

Run this single command and read its output. If an argument was given, it is the start date `$ARGUMENTS`; otherwise use the date 21 days before today.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_census.py" --since <start date>
```

Add `--no-titles` if the user asks for output they can share: it leaves session titles out.

Then summarise the result in plain words, in at most 12 lines:
- which tier and role (main session or subagent) account for most of the estimated spend;
- how much of the context processed was cache re-reads, and how many sessions crossed 200k;
- the repeat reads and tool results that cost the most;
- how many subagent dispatches named no model, and how many forks inherited a large context;
- the one or two changes most likely to cut spend (for example: name a tier on every dispatch, start fresh sessions at natural breaks, grep before reading whole files).

Dollar figures are API list-price estimates used as a weight, not a bill. Do not copy session titles into any file.

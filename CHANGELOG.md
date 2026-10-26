# Changelog

In brief. Each version's [release note](https://github.com/aniteshdharam/claude-token-policy/releases)
says why as well.

## 0.1.5

- New `agents/reviewer.md`: the strong tier at high effort, read-only, for reviews of ordinary
  work (a fresh read of a delta, a re-read after a fix, the security read). A review of a
  critical surface keeps the top everyday effort. Why: a week's count of the helper agents on
  one machine found 266 reviewers at the top setting with no rule behind it (an agent with no
  effort of its own takes the one set for its model), more than half of all helper output; on
  the rest, sessions picked medium, high or max as they went.
- Policy and skill: review effort follows risk, in rule 3 and the review row.

## 0.1.4

- Checkpoint: the session's carried-forward statements are labelled as its own claims, to be
  checked before they are built on, and the "files already in hand" list no longer says "do not
  blindly re-read"; it says to read the range before acting on a remembered detail. A wrong
  conclusion written before compaction no longer comes back reading as fact. New test in
  `tests/test_hooks.py`.
- Policy and skill: a section on keeping the context true as well as small: check a conclusion
  before writing it into a hand-off or memory, rewrite a superseded note instead of appending
  the correction, give a session only the tools it needs.
- POLICY.md: the long-session rule now quotes the corrected figure (31 compactions), not the 70
  and 104 the 0.1.2 correction withdrew.

## 0.1.3

- Turn guard: says once per session, at any context size, when automatic compaction is switched
  off (`autoCompactEnabled: false`, or `DISABLE_COMPACT` / `DISABLE_AUTO_COMPACT` in the
  environment), and names where. Before this it kept promising "runway" before a compaction
  that would never come. The `/config` Auto-compact toggle writes that setting without a trace
  in any transcript; on the author's machine it went unnoticed overnight, compactions fell from
  about 100 a day to 3 and sessions ran past 600k. Settings are now read in Claude Code's own
  order, managed settings first. Six new tests in `tests/test_hooks.py`.

## 0.1.2

- Census: each real file is read once, so a project folder reached through a symlink is no
  longer counted twice; each reply counts once even when a transcript carries a copy of it;
  compactions are the main session's own, not its subagents'. New test: `tests/test_census.py`.
- README: the all-time usage picture set against the census, a dated correction (totals were
  about 1.8 times too high over the last 30 days; ratios should hold; the longest session
  compacted 31 times, not 70 or 104), and a dated learning-curve table.

## 0.1.1

- Hooks: the per-event markers in `~/.claude/token-policy-state` are now pruned by ordinary
  events (one in sixteen), not only when a file is read a second time. Before this the folder
  grew by one small file per tool call.
- Dispatch guard: refusal text names the tiers the same way the README does (top, strong, fast).
- Census: `--help` describes every flag; the default window is 21 days, matching `/token-census`;
  `--no-titles` leaves session titles out of the output; the output states when the price
  table was last checked.
- README: requirements, how to shift the tiers when Opus is your top model, how to run the tests.
- CI: the test suite runs on every push and pull request (Linux and macOS, Python 3.9 and 3.13).

## 0.1.0

- First public release: the skill, five hooks, the builder agent, the census and the status line.

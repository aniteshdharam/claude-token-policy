# Changelog

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

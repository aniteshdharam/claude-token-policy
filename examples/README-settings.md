# Recommended settings

Merge the keys in `settings.json` into your own `~/.claude/settings.json`.

- `model`: the family name (`opus`), never a pinned version, so the session follows the newest model of that family.
- `autoCompactWindow`: an absolute token count at which compaction is configured. The turn-guard hook derives its warning line from this value (77% of it), so changing it moves the warning too.
- `env.CLAUDE_CODE_SUBAGENT_MODEL`: the model an unnamed subagent dispatch runs on, instead of inheriting the parent's.
- `statusLine`: runs the status-line script. Copy `scripts/statusline.sh` from the plugin to `~/.claude/statusline.sh` first; it needs `jq`.

Optional plugin knobs go in the same `env` block, for example `TOKEN_POLICY_MODE` set to `warn` to turn every refusal into a warning. The full list is at the top of `hooks/_config.py`.

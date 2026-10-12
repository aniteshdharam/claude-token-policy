#!/bin/bash
# Status line: shows context in ABSOLUTE tokens, because the bill is context x calls.
# Colours: green < 120k, yellow 120-200k, red >= 200k, "checkpoint" note >= 300k,
# whatever the model's window. Override the last line with TOKEN_POLICY_STATUS_CHECKPOINT.
# Depends only on jq (reads the status-line JSON on stdin) and, optionally, git.
input=$(cat)

MODEL=$(echo "$input" | jq -r '.model.display_name // "Claude"')
COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
WIN=$(echo "$input" | jq -r '.context_window.context_window_size // 200000' | cut -d. -f1)
CUR=$(echo "$input" | jq -r '(.context_window.current_usage // {}) | ((.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0))' | cut -d. -f1)
EFFORT=$(echo "$input" | jq -r '.effort.level // empty')

CHECKPOINT=${TOKEN_POLICY_STATUS_CHECKPOINT:-300000}
case "$CHECKPOINT" in ''|*[!0-9]*) CHECKPOINT=300000;; esac
case "$PCT" in ''|*[!0-9]*) PCT=0;; esac
case "$WIN" in ''|*[!0-9]*) WIN=200000;; esac
case "$CUR" in ''|*[!0-9]*) CUR=0;; esac
USED=$CUR
[ "$USED" -eq 0 ] && USED=$(( PCT * WIN / 100 ))
USEDK=$(( USED / 1000 )); WINK=$(( WIN / 1000 ))

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; MAGENTA='\033[35m'; RESET='\033[0m'

if git rev-parse --git-dir > /dev/null 2>&1; then
    BRANCH="($(git branch --show-current 2>/dev/null))"
else
    BRANCH="(no git)"
fi

CTX_NOTE=""
if [ "$USED" -ge "$CHECKPOINT" ]; then CTX_COLOR="$RED"; CTX_NOTE=" checkpoint"
elif [ "$USED" -ge 200000 ]; then CTX_COLOR="$RED"
elif [ "$USED" -ge 120000 ]; then CTX_COLOR="$YELLOW"
else CTX_COLOR="$GREEN"; fi

EFFORT_SEG=""
[ -n "$EFFORT" ] && EFFORT_SEG=" | Effort: ${MAGENTA}${EFFORT}${RESET}"

echo -e "${CYAN}[$MODEL]${RESET} ${BRANCH} | Context: ${CTX_COLOR}${USEDK}k/${WINK}k (${PCT}%)${CTX_NOTE}${RESET}${EFFORT_SEG} | Spent: ${GREEN}\$$(printf "%.2f" "$COST")${RESET}"

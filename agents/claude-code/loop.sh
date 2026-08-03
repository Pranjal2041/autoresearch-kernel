#!/usr/bin/env bash
# Claude Code agent loop.
#
# Each iteration gives Claude a fresh context: the experiment rules, the
# objective, and the submit history fetched live from the kernel API. Claude
# makes one focused change in /workspace, submits it, polls for the score,
# and exits the turn. The loop then starts the next iteration. Continuity
# across iterations comes from the API history and workspace notes, exactly
# like program.md-style autoresearch.
set -u

LOOPS="${AR_MAX_LOOPS:-0}"
ITERATION=0

while :; do
  ITERATION=$((ITERATION + 1))
  if [ "$LOOPS" != "0" ] && [ "$ITERATION" -gt "$LOOPS" ]; then
    echo "[loop] reached AR_MAX_LOOPS=$LOOPS, exiting"
    exit 0
  fi

  # If the kernel is gone or stopping, stop looping.
  if ! curl -sf "$AR_API_URL/health" > /dev/null; then
    echo "[loop] kernel API unreachable, exiting"
    exit 0
  fi

  HISTORY="$(curl -sf "$AR_API_URL/history" | tail -c 24000)"
  BEST="$(curl -sf "$AR_API_URL/best" || echo 'null')"
  BUDGET="$(curl -sf "$AR_API_URL/experiment" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["budget"]))' 2>/dev/null || echo '{}')"

  echo "[loop] iteration $ITERATION starting (best: $BEST)"

  claude -p "$(cat "$AR_RULES")

## Your objective
$AR_OBJECTIVE

## Budget
$BUDGET

## Current best submit
$BEST

## Full submit history (JSON)
$HISTORY

$(cat "$AR_AGENT_DIR/prompt.md")" \
    --dangerously-skip-permissions \
    ${AR_CLAUDE_MODEL:+--model "$AR_CLAUDE_MODEL"} \
    || { echo "[loop] claude exited nonzero, backing off"; sleep 15; }

  sleep 2
done

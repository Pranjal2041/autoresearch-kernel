#!/usr/bin/env bash
# Codex agent loop. Same design as the claude-code loop: fresh context per
# iteration, memory via the kernel API history and workspace notes.
set -u

LOOPS="${AR_MAX_LOOPS:-0}"
ITERATION=0

while :; do
  ITERATION=$((ITERATION + 1))
  if [ "$LOOPS" != "0" ] && [ "$ITERATION" -gt "$LOOPS" ]; then
    echo "[loop] reached AR_MAX_LOOPS=$LOOPS, exiting"
    exit 0
  fi
  if ! curl -sf "$AR_API_URL/health" > /dev/null; then
    echo "[loop] kernel API unreachable, exiting"
    exit 0
  fi

  HISTORY="$(curl -sf "$AR_API_URL/history" | tail -c 24000)"
  BEST="$(curl -sf "$AR_API_URL/best" || echo 'null')"
  BUDGET="$(curl -sf "$AR_API_URL/experiment" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["budget"]))' 2>/dev/null || echo '{}')"

  echo "[loop] iteration $ITERATION starting (best: $BEST)"

  codex exec \
    --dangerously-bypass-approvals-and-sandbox \
    ${AR_CODEX_MODEL:+-m "$AR_CODEX_MODEL"} \
    --cd "$AR_WORKSPACE" \
    "$(cat "$AR_RULES")

## Your objective
$AR_OBJECTIVE

## Budget
$BUDGET

## Current best submit
$BEST

## Full submit history (JSON)
$HISTORY

$(cat "$AR_AGENT_DIR/prompt.md")" \
    || { echo "[loop] codex exited nonzero, backing off"; sleep 15; }

  sleep 2
done

#!/usr/bin/env bash
# Codex agent loop: fresh context per iteration, memory via the kernel API
# history and workspace notes.
#
# CODEX_HOME design (ported from the econ kernel's codex provider): build
# a private per-instance home at startup — auth.json copied 0600 from the
# read-only shared mount (host ~/.codex as a local-runner fallback), and a
# generated config.toml declaring model, reasoning effort, and sandbox
# policy — instead of flag soup on every exec and a contended rw ~/.codex.
set -u

LOOPS="${AR_MAX_LOOPS:-0}"
ITERATION=0

# ── per-instance CODEX_HOME ──────────────────────────────────────────
# HOME, not tmp: codex refuses helper binaries under temporary dirs
CODEX_HOME="${HOME:-/tmp}/.ar-codex-home-$$"
mkdir -p "$CODEX_HOME"
chmod 700 "$CODEX_HOME"
for AUTH_SOURCE in "${AR_CODEX_SHARED:-/codex-shared}/auth.json" "$HOME/.codex/auth.json"; do
  if [ -f "$AUTH_SOURCE" ]; then
    cp "$AUTH_SOURCE" "$CODEX_HOME/auth.json"
    chmod 600 "$CODEX_HOME/auth.json"
    break
  fi
done
if [ ! -f "$CODEX_HOME/auth.json" ]; then
  echo "[loop] no codex auth found (shared mount or ~/.codex); run 'ar auth codex'"
  exit 1
fi
{
  [ -n "${AR_CODEX_MODEL:-}" ] && printf 'model = "%s"\n' "$AR_CODEX_MODEL"
  printf 'model_reasoning_effort = "%s"\n' "${AR_CODEX_EFFORT:-high}"
  printf 'approval_policy = "never"\n'
  printf 'sandbox_mode = "danger-full-access"\n'
} > "$CODEX_HOME/config.toml"
export CODEX_HOME
echo "[loop] CODEX_HOME=$CODEX_HOME model=${AR_CODEX_MODEL:-<default>} effort=${AR_CODEX_EFFORT:-high}"

# Inactivity ceiling per iteration (econ-kernel hardening): a hung exec
# must not eat the run. coreutils timeout when present, bare exec if not.
run_codex() {
  if command -v timeout > /dev/null 2>&1; then
    timeout --kill-after=30 "${AR_CODEX_TIMEOUT:-2400}" codex exec "$@"
  else
    codex exec "$@"
  fi
}

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

  run_codex \
    --cd "$AR_WORKSPACE" \
    --skip-git-repo-check \
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
    < /dev/null \
    || { echo "[loop] codex exited nonzero, backing off"; sleep 15; }

  sleep 2
done

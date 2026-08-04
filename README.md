# autoresearch-kernel

The autoresearch kernel: a universal abstraction for autoresearch experiments. An experiment is a
folder with seed files, rules, and an objective, where the objective is a
command that prints a metric. To run it you pick an agent and a runner. The
runner builds a sandbox for this machine. The agent is any process that edits
the workspace and calls submit. Each submit snapshots the workspace, scores
it in a clean sandbox, and logs the result. Best snapshot wins. A dashboard
watches the log.

`DESIGN.md` is the contract. Code follows it.

## Install

```
pip install git+https://github.com/Pranjal2041/autoresearch-kernel
```

The `ark` CLI, the kernel, and the bundled agents (claude-code, codex,
random-search, dummy) all ship in the package: no checkout needed.
For development, clone and `uv pip install -e ".[dev]"` instead.

## Quickstart

```
ark init experiments/my_experiment     # scaffold a working experiment
ark validate experiments/my_experiment
ark run experiments/my_experiment --agent dummy --runner local --name demo
ark watch runs/demo            # dashboard at http://127.0.0.1:8722
ark history runs/demo          # submit table in the terminal
ark diff runs/demo 2 3         # what changed between two submits
ark help                       # full documentation, `ark help design` for the contract
```

Runners: `local` (subprocess, no isolation, instant), `apple` (Apple
containers, the containerized default on macOS, needs `container system
start` once per boot), `docker`.

Agents live in `agents/<name>/` and are selected with `--agent <name>` or a
path. `dummy` is a scripted test agent. `claude-code` runs Claude Code in a
fresh-context loop. With the local runner the host `claude` CLI is used
as-is. For container runners, set up credentials once:

```
ark auth claude-code --runner apple
```

This seeds `~/.claude-ar` from existing host credentials when they exist as
a file. On macOS the host CLI keeps tokens in the Keychain, so instead it
drops you into a one-time login shell inside the agent's container image
(`claude`, then `/login`, then exit). The directory is mounted into every
future agent container, and the built agent layer is cached and reused by
`ark run`.

## An experiment is a folder

```
experiments/my_experiment/
  experiment.yaml    objective, environment, tracking, budgets
  rules.md           free-form rules and context for the agent
  seed/              initial workspace contents
  eval/              eval harness; the agent can never see or touch it
```

```yaml
name: my-experiment
objective:
  metric: val_loss
  direction: minimize
  mode: official               # kernel-verified; "reported" = agent self-reports
  eval_command: python3 /eval/run.py --split val --out /result/metrics.json
  metric_path: metrics.json:val_loss
  timeout_seconds: 900
  test_command: python3 /eval/run.py --split test --out /result/metrics.json
submit:
  signature:
    notes: {type: string, required: true}
environment:
  base: python:3.12-slim       # or a Dockerfile path inside the experiment
  resources: {cpu: 8, mem_gb: 32, gpu: 0, net: true}
  setup:                       # runs once, cached as an image layer
    - pip install -r /workspace/requirements.txt
stop:
  max_submits: 200
  max_hours: 12
```

Commands are written against canonical paths (`/workspace`, `/eval`,
`/result`, `/agent`, `/rules.md`). Container runners mount them literally,
the local runner rewrites them. Programs running inside a sandbox locate
them through `AR_WORKSPACE`, `AR_EVAL`, `AR_RESULT`, never by hardcoding.

## The kernel API

The agent's whole world is HTTP (`AR_API_URL` inside the sandbox):

| Route | Meaning |
|---|---|
| `POST /submit` | checkpoint: snapshot + score. One at a time, 409 if busy. |
| `GET /submit/{id}` | the record, including the score once eval lands |
| `GET /history` | all records. Test metrics are stripped, always. |
| `GET /best` | best submit so far |
| `GET /experiment` | objective, rules, remaining budget |

Every submit is one git commit in a kernel-owned repo outside the sandbox.
Files of 5MB or more are recorded as hashes in a manifest instead of stored.
Dotfiles and junk directories are never tracked. The agent's own git usage
cannot touch kernel history.

## Tests

```
.venv/bin/python -m pytest tests/
```

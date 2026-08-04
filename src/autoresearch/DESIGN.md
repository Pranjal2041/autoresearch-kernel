# Autoresearch: Design

Frozen 2026-08-03. This document is the contract. Code follows it, not the other way around.

## What this is

A universal abstraction for autoresearch experiments. An experiment is a folder with seed files, rules, and an objective, where the objective is a command that prints a metric. To run it you pick an agent and a runner. The runner builds a sandbox for this machine. The agent is any process that edits the workspace and calls submit. Each submit snapshots the workspace, optionally scores it in a clean sandbox, and logs the result. Best snapshot wins. A dashboard watches the log.

Today, every autoresearch-style project (Karpathy's speedrun, proposer-solver games, GEPA runs) rebuilds this infrastructure from scratch. Here, the infrastructure is built once and every experiment is just content.

## Principles

1. **Simplicity.** If the design cannot be explained in under 200 words, it is not simple. The paragraph above is the test, and it must stay true.
2. **Modularity.** Adding an agent, runner, or experiment touches only its own folder. Pillars interact only through contracts.
3. **Generality.** Any autoresearch design that appears in academia or industry should be expressible in this interface without kernel changes.
4. **Scalability.** Scaling infra, agents, or experiment count must not require redesign. Parallelism and remote execution live inside runner implementations.

## Pillars

### 1. Experiment (content)

A folder. Defines the game, not who plays it.

```
experiments/nanogpt_speedrun/
  experiment.yaml    # objective, environment, tracking, budgets
  rules.md           # free-form rules and context given to the agent
  seed/              # initial workspace contents
  eval/              # eval harness, never writable by the agent
```

`experiment.yaml` (draft schema, the reference example):

```yaml
name: nanogpt-speedrun
description: Minimize validation loss of a GPT trained for 5 minutes.

objective:
  metric: val_loss
  direction: minimize          # minimize | maximize
  mode: official               # official | reported
  eval_command: python /eval/run.py --split val --out /result/metrics.json
  metric_path: metrics.json:val_loss
  timeout_seconds: 900
  test_command: python /eval/run.py --split test --out /result/metrics.json
                               # optional. kernel-only. agent never sees results.

submit:
  signature:                   # user-defined payload the agent must send
    notes: {type: string, required: true}
    # reported-mode experiments add fields like val_loss: {type: number}

environment:
  base: python-3.12-cuda       # preset name or path to a Dockerfile
  resources: {cpu: 8, mem_gb: 32, gpu: 1, net: true}
  setup:                       # runs once, then the image is cached
    - pip install -r /workspace/requirements.txt
    - python /eval/download_data.py

tracking:
  max_file_mb: 5
  extra_ignores: [__pycache__/, node_modules/, wandb/]

stop:
  max_submits: 200
  max_hours: 12
  target: null                 # stop early if the metric reaches this
```

Notes:

- `mode: official` means the kernel runs `eval_command` in a fresh eval sandbox and the score is verified. `mode: reported` means submit is a logging checkpoint and the agent self-reports the fields in `signature`. Trusting the report is the experiment author's declared choice. Every record carries its mode so the two are never confused.
- If the rules allow the agent to change dependencies, the dependency file is part of the workspace and the eval sandbox installs it. Nothing crosses the boundary implicitly.
- Train/val/test splits are the experiment author's concern, expressed through the two commands. The agent optimizes val. The kernel quietly runs test on scored submits and writes it only to the log. Overfitting shows up on the dashboard as val-test divergence.

### 2. Runner (infrastructure)

Turns "give me a sandbox with these resources" into reality on this machine. The interface:

- `build(image_spec)` returns a cached image digest. Setup commands run once at build, like gym-anything's post_start cache.
- `provision(image, resources, mounts, env)` returns a sandbox handle.
- `exec(sandbox, command, timeout)` returns exit code and output.
- `destroy(sandbox)`.

Requirement: the workspace is a host bind mount. Every planned backend supports this (Apple containers as the containerized default on macOS, Docker, Apptainer with fakeroot, bare subprocess for dev). A future runner that cannot mount (remote Firecracker fleet, Slurm) instead implements one extra capability, `export(sandbox, path)`, and the kernel syncs after each submit. That difference stays invisible above the runner.

Backends are selected by name at run time and live in their own folders. Adding one touches nothing else.

### 3. Agent (search policy)

Anything that edits the workspace and calls the HTTP API. The kernel never knows what is inside. A Claude Code loop, a Codex loop, GEPA, or a multi-agent swarm are all just agents.

An agent is a folder with a launch spec: the command to start, image layer additions (CLI installs), credential mounts, and env vars it needs. The kernel injects `AR_API_URL` and `AR_WORKSPACE`, and places `rules.md` and the objective description where the agent can read them.

The agent owns its inner loop entirely. It may run anything, delete anything, use git internally, or spawn its own subprocesses inside its sandbox. Keep-versus-revert is agent policy, not kernel policy. The kernel only records and reports best-so-far.

### 4. Core (kernel)

Owns the contracts and the bookkeeping. Contains:

- CLI: `ark validate`, `ark run <experiment> --agent <name> --runner <name> --name <run>`, `ark watch`, `ark history`, `ark diff <i> <j>`, `ark resume`.
- The submit transaction (below).
- Workspace tracking (below).
- Registry resolving agents and runners by name.
- Event log: append-only JSONL, the single source of truth. Snapshot plus live stream to viz clients.
- Dashboard: a web app that is a pure observer of the event log and the git history. It can never influence a run.
- Resume: log plus git is sufficient to reconstruct all state.

## The submit contract

Submit is a checkpoint, not an eval trigger.

- Transport is HTTP only. No MCP, no SDK requirement. Anything that can curl can be an agent.
- One submit at a time. A submit during an active one is rejected, not queued. The agent is free to parallelize anything internally. Submit is the serialization point that gives the log a total order.
- The snapshot is synchronous: submit commits the workspace and returns a submit id immediately. Scoring is asynchronous: the agent polls for the result. A blocking convenience flag may exist, but the API is shaped async from day one.
- Snapshot atomicity is by convention: the agent quiesces before calling submit, the same contract every checkpointing system has.
- In official mode, eval materializes the workspace into a fresh eval sandbox built from the cached image. The agent never runs the scorer, never sees test data, and cannot self-report. In reported mode, the kernel logs the payload and still snapshots, so a reported run can always be re-verified later. Reported mode is deferred verification, not absent verification.

Endpoints:

```
POST /submit            payload per experiment signature, returns {submit_id}
GET  /submit/{id}       record including score when ready
GET  /history           all records, test metrics stripped
GET  /best              best submit so far
GET  /experiment        objective, rules, budgets remaining
```

The agent-visible API never leaks test metrics. Only the log and dashboard have them.

Submit record (draft schema):

```json
{
  "submit_id": 42,
  "time": "2026-08-03T12:00:00Z",
  "commit": "abc123",
  "image_digest": "sha256:...",
  "mode": "official",
  "status": "scored",
  "metric": {"name": "val_loss", "value": 3.182},
  "test_metric": {"name": "val_loss", "value": 3.221},
  "payload": {"notes": "switched to rotary embeddings"},
  "large_files": [{"path": "data/tokens.bin", "size_mb": 412, "sha256": "..."}],
  "best_so_far": true
}
```

Provenance of any attempt is two ids: image digest plus commit hash.

## The tracking contract

- The kernel owns a git repository whose git-dir lives outside the sandbox and whose work tree is the mounted workspace. There is no `.git` inside the sandbox. The agent cannot tamper with history and its own git usage cannot collide with the kernel's.
- On each submit the kernel snapshots the full tree. Track by default, exclude by predicate:
  - files of 5MB or more: hash and size recorded in a versioned `large_files.json` manifest, bytes not stored,
  - paths starting with `.`: ignored,
  - a small default junk list (`__pycache__/`, `node_modules/`, `venv/`, `wandb/`, and similar), extendable per experiment.
- Both the size threshold and ignore lists are defaults in `tracking:`, not constants.
- Official eval materializes large files directly from the live workspace at submit time. Git is the history, not the transport. The manifest hash attests that what eval consumed matches the snapshot.
- One kernel commit per submit, nothing else in the history. Reverting the workspace is never done by the kernel.

## Decisions log

| Decision | Choice | Reason |
|---|---|---|
| Experiment identity | seed + rules + objective only | agent, runner, budget are run config, so results stay comparable across agents |
| Who owns the loop | agent owns its process, kernel owns the transaction | one contract covers Claude loops, GEPA, and swarms |
| Scoring trust | per-experiment `official` or `reported` | trust model is the author's declared choice, always marked in the record |
| API transport | HTTP only | universal, zero integration burden, no MCP |
| Submit concurrency | serialized, reject not queue | submit is a deliberate checkpoint with a total order |
| Submit blocking | sync snapshot, async score | long evals must not hold an HTTP connection open |
| History mechanism | git with kernel-side git-dir over the workspace mount | full-tree capture including new files, tamper-proof, no sync step, runner-portable |
| Container overlays | rejected | runner-specific and not diffable, violates runner swap |
| Large files | hash manifest by default, bytes not stored | provenance without storage blowup, CAS is a later opt-in |
| Eval isolation | fresh sandbox from cached image per official eval | hermetic scores, agent mess is irrelevant, anti reward-hacking boundary |
| Test split | kernel-only, stripped from agent API | overfitting becomes visible instead of invisible |
| Dashboard | pure observer of log and git | proven pattern in both reference designs |
| Parallel agents | per-agent workspaces and branches, one shared object store via git plumbing, one submit lane | branches keep histories clean, shared objects keep cross-agent diffs, one lane keeps the log totally ordered |
| Viz hook | optional objective.viz_command in the eval sandbox, best-effort | seeing a solution is experiment-defined; a broken viz never fails a submit |

## Build plan

1. **Schemas.** experiment.yaml and the submit record, validated by `ark validate`. (This document is the spec.)
2. **Core loop.** Experiment loader, subprocess runner, HTTP API, tracking, JSONL log. Prove it end to end with a toy experiment and a scripted dummy agent.
3. **Real run.** Docker runner, Claude Code agent, a speedrun-style experiment. First overnight run.
4. **Dashboard.** Web app over events.jsonl and git: metric over submits, val versus test, diffs between attempts, live feed.
5. **Breadth.** Apple container and Apptainer runners. Codex and GEPA agents. This is where modularity is proven: no kernel edits allowed.
6. **Deferred.** Content-addressed large-file store, Firecracker runner, oracle messages to a running agent. (Parallel agents, the Apptainer runner for Slurm, and the viz hook shipped in phase 6 of the build.)

## Prior art

- [karpathy/autoresearch](https://github.com/karpathy/autoresearch): the loop, program.md, the 5-minute attempt budget.
- econ_simulation_kernel (local, `~/Developer/simulation_envs`): strong kernel with declarative environment folders, event bus as single source of truth, dual runner backends, viz as observer.
- [cmu-l3/gym-anything](https://github.com/cmu-l3/gym-anything): env.json and task.json notation, base image presets, setup-hook caching, swappable agent contract.
- [gepa-ai/gepa](https://github.com/gepa-ai/gepa) optimize_anything: seed plus evaluator plus objective as a universal API.
- [paperfoot/autoresearch-cli](https://github.com/paperfoot/autoresearch-cli): toml notation, bring-your-own-agent, terminal dashboard, git keep/revert.

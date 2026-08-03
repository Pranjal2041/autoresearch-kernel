"""Help texts for the `ar` CLI.

Long-form documentation lives here, one entry per command and per concept
topic, so `ar help <thing>` can explain semantics the way `uv help <cmd>`
does: prose first, then usage, then examples.
"""

OVERVIEW = """\
autoresearch: run agents in a loop against a measurable objective.

An experiment is a folder with seed files, rules, and an objective, where
the objective is a command that prints a metric. To run it you pick an
agent and a runner. The agent is any process that edits the workspace and
calls submit over HTTP. Each submit snapshots the workspace into git,
scores it in a clean sandbox, and logs the result. Best snapshot wins.

Usage: ar <COMMAND> [OPTIONS]

Commands:
  init       Scaffold a new experiment folder
  validate   Check an experiment folder against the schema
  run        Run an experiment with an agent on a runner
  watch      Serve the web dashboard for a run or a runs directory
  list       List runs, experiments, agents, and runners
  history    Print the submit table of a run
  best       Print the best submit record of a run
  diff       Show what changed between two submits
  auth       Set up an agent's credentials for container runners
  help       Detailed documentation for a command or concept

Concepts (ar help <concept>):
  experiment   The folder anatomy and every experiment.yaml field
  agents       What an agent is and how to add one
  runners      local, apple, docker, and the sandbox contract
  tracking     How workspace history works (git, large files, ignores)
  api          The kernel HTTP API an agent talks to
  design       The full frozen design contract (DESIGN.md)

Examples:
  ar validate experiments/circle_packing
  ar run experiments/circle_packing --agent claude-code --runner apple
  ar watch runs                # dashboard over every run, newest first
  ar history runs/my_run
  ar help run

`ar help design` prints the full frozen design contract.
"""

COMMANDS: dict[str, str] = {
    "init": """\
Scaffold a new experiment folder.

Creates a complete, working official-mode experiment: a commented
experiment.yaml, a rules.md skeleton, a trivial seed candidate, and an
eval stub that scores it. The result passes `ar validate` and runs
end-to-end with the random-search agent immediately, so you start from a
green pipeline and edit, rather than debugging a blank page.

Edit order that works well: eval/run.py (the scorer defines the game),
then experiment.yaml (metric, environment, budgets), then rules.md (what
the agent is told), then seed/.

Usage: ar init <path>

Examples:
  ar init experiments/kernel_speedrun
  ar validate experiments/kernel_speedrun
  ar run experiments/kernel_speedrun --agent dummy
""",
    "list": """\
List runs, experiments, agents, and runners.

Runs come from --runs-dir (default ./runs) with their experiment, agent,
runner, submit count, best score, and status. Experiments and agents are
discovered from ./experiments and ./agents in the working directory.
Runners are the registered backends.

Usage: ar list [all | runs | experiments | agents | runners] [--runs-dir D]

Examples:
  ar list
  ar list runs --runs-dir /scratch/ar-runs
""",
    "validate": """\
Check an experiment folder against the schema.

Loads experiment.yaml, verifies the folder contents (seed/, rules.md, and
eval/ when the mode is official), and prints a one-screen summary of the
objective, submit signature, environment, and stop conditions. Validation
is strict: unknown top-level keys are errors, so typos fail here instead
of hours into a run.

Reported-mode experiments are additionally checked for a required number
field in the submit signature matching the objective metric, since that
field is the self-reported score.

Usage: ar validate <experiment>

Examples:
  ar validate experiments/circle_packing
""",
    "run": """\
Run an experiment with an agent on a runner.

Creates a run directory under --runs-dir containing the workspace (seeded
from the experiment's seed/), the kernel-owned git repository, and
events.jsonl, the append-only log that is the single source of truth.
Builds the environment image (cached by content hash of the environment
section), starts the kernel HTTP API, launches the agent in its sandbox,
and supervises until a stop condition fires:

  - stop.max_submits reached
  - stop.max_hours elapsed
  - stop.target metric value reached
  - the agent process exits on its own
  - ctrl-c (the run shuts down cleanly and can be resumed)

The agent's stdout goes to <run>/agent.log. Official evals run in fresh
sandboxes; their logs land in <run>/evals/<submit>/.

The experiment defines the game; --agent and --runner are run
configuration, so the same experiment is comparable across agents.

Usage: ar run <experiment> --agent <name|path> [OPTIONS]

Options:
  --agent      agent name (looked up under ./agents) or a path to an
               agent folder. Required. Repeat for parallel agents, and
               mixing types is allowed (a claude-code and a random-search
               can race on one experiment). Each instance gets its own
               workspace and git branch; all share one submit lane
               (retry on 409) and one global best.
  --runner     local | apple | docker (default: local). See `ar help
               runners` for the trade-offs; local has no isolation.
  --name       run name (default: timestamped). Also the dashboard label.
  --runs-dir   parent directory for run folders (default: ./runs)
  --resume     continue an existing run: replays events.jsonl, marks any
               submit orphaned by a crash as failed, and keeps numbering.
  --agent-env  KEY=VALUE overrides for the agent's environment,
               repeatable. e.g. --agent-env AR_CLAUDE_MODEL=claude-sonnet-5

Examples:
  ar run experiments/toy_quadratic --agent dummy
  ar run experiments/circle_packing --agent claude-code --runner apple \\
      --agent-env AR_CLAUDE_MODEL=claude-sonnet-5 --name sonnet_apple
  ar run experiments/circle_packing --agent claude-code --name sonnet_apple --resume
""",
    "watch": """\
Serve the web dashboard.

The dashboard is a pure observer: it reads events.jsonl and the kernel git
repository fresh on every request and can never influence a run. It works
identically on live and finished runs.

Point it at a single run directory, or at a parent directory (like ./runs)
to get a run picker over every run, defaulting to the most recently
active. Live runs refresh every two seconds.

Views: submit rail with per-submit gains, metric chart (val and the
kernel-only test series), and per-submit inspection: full notes, exact
metrics, the complete workspace file tree at that submit's commit with
file contents, official eval logs, and a diff against any other submit.

Usage: ar watch <run_dir | runs_dir> [--port N]

Options:
  --port   port to bind on 127.0.0.1 (default: 8722)

Examples:
  ar watch runs
  ar watch runs/sonnet_apple --port 9000
""",
    "history": """\
Print the submit table of a run.

One row per submit: status, val metric, kernel-only test metric, a star
for submits that advanced the best, the snapshot commit, and the agent's
note (or the failure reason). Reads only events.jsonl, so it works on
live runs and crashed runs alike.

Usage: ar history <run_dir> [--json]

Options:
  --json   emit the full records as JSON instead of a table

Examples:
  ar history runs/sonnet_apple
  ar history runs/sonnet_apple --json | jq '.[] | select(.best_so_far)'
""",
    "best": """\
Print the best submit record of a run as JSON.

The best is the last submit marked best_so_far in the event log. The
record includes the exact metric value, the snapshot commit (from which
the winning code can be checked out), the environment image digest, and
the agent's note.

Usage: ar best <run_dir>

Examples:
  ar best runs/sonnet_apple
  ar best runs/sonnet_apple | jq -r .commit
""",
    "diff": """\
Show what changed between two submits.

A unified git diff between the two snapshot commits, straight from the
kernel-owned repository. Every submit is exactly one commit, so this
answers "what did the agent actually change between attempt A and B".
Large files (hash-only in the manifest) appear as binary changes.

Usage: ar diff <run_dir> <a> <b>

Examples:
  ar diff runs/sonnet_apple 1 2
  ar diff runs/sonnet_apple 9 13 | less
""",
    "auth": """\
Set up an agent's credentials for container runners.

Agents that need credentials declare mounts in agent.yaml (for example
~/.claude-ar mounted at /root/.claude). This command prepares that host
directory once; afterwards every agent container mounts it.

Two paths, tried in order:

1. Seeding. If the agent declares auth_seed and the host has a plain
   credentials file (a Linux host's ~/.claude/.credentials.json), it is
   copied in and no login is needed.
2. Interactive login. Builds the agent's container layer on --base and
   drops you into a shell inside it with the credentials directory
   mounted, printing the agent's login instructions. Whatever the login
   writes persists on the host. This is the path on macOS, where the
   host CLI keeps tokens in the Keychain rather than a file.

Usage: ar auth <agent> [OPTIONS]

Options:
  --runner  apple | docker (default: apple)
  --base    base image for the login shell (default: python:3.12-slim)
  --shell   open the interactive shell even if seeding succeeded

Examples:
  ar auth claude-code
  ar auth claude-code --runner docker --shell
""",
    "help": """\
Display documentation for a command or concept.

Usage: ar help [command | concept]

Examples:
  ar help
  ar help run
  ar help experiment
""",
}

CONCEPTS: dict[str, str] = {
    "experiment": """\
An experiment is a folder. It defines the game, not who plays it.

  experiments/my_experiment/
    experiment.yaml    objective, environment, tracking, budgets
    rules.md           free-form rules and context given to the agent
    seed/              initial workspace contents
    eval/              eval harness; the agent can never see or touch it

experiment.yaml, field by field:

  name, description   identity and one-line summary.

  objective:
    metric            the number being optimized, e.g. val_loss
    direction         minimize | maximize
    mode              official: the kernel runs eval_command in a fresh
                      sandbox and the score is verified.
                      reported: submit is a logging checkpoint and the
                      agent self-reports the metric; trusting it is the
                      experiment author's declared choice. Every record
                      carries its mode so the two are never confused.
    eval_command      command that scores the workspace (official mode).
                      Writes a metrics file under /result.
    metric_path       where the kernel reads the number, as
                      "<file>.json:<dotted.key>", e.g. metrics.json:val_loss
    timeout_seconds   hard wall-clock cap on one eval
    test_command      optional. A held-out split run by the kernel on
                      scored submits. Results go only to the event log
                      and dashboard; the agent API never shows them, so
                      overfitting appears as val-test divergence.

  submit:
    signature         the payload schema for POST /submit, name to
                      {type: string|number|boolean, required: bool}.
                      Reported mode must include a required number field
                      named after the metric.

  environment:
    base              a registry image (python:3.12-slim) or a path to a
                      Dockerfile inside the experiment folder
    resources         {cpu, mem_gb, gpu, net}
    setup             shell commands run once and cached into the
                      environment image (pip installs, data downloads)

  tracking:
    max_file_mb       files at or above this are recorded as hashes in a
                      manifest, bytes not stored (default 5)
    extra_ignores     gitignore-style additions to the junk list

  stop:
    max_submits, max_hours, target
                      at least one of the first two is required;
                      unbounded runs must be explicit.

Commands are written against canonical paths (/workspace, /eval, /result)
and are rewritten or mounted per runner. Programs running inside a
sandbox locate them via AR_WORKSPACE, AR_EVAL, AR_RESULT, never by
hardcoding. See `ar help runners`.
""",
    "agents": """\
An agent is any process that edits the workspace and calls the kernel
HTTP API. The kernel never knows what is inside: a Claude Code loop, a
Codex loop, GEPA, or a multi-agent swarm are all just agents.

An agent is a folder with agent.yaml:

  name, description   identity
  command             what to launch inside the agent sandbox
  env                 extra environment variables (overridable per run
                      with `ar run --agent-env KEY=VALUE`)
  mounts              extra mounts, typically credentials
  image_setup         container-layer commands (CLI installs), cached
                      per image. Ignored by the local runner, which
                      expects the tools on the host.
  auth_seed           where `ar auth` may copy credentials from
  auth_help           login instructions `ar auth` prints in its shell

The kernel injects AR_API_URL, AR_WORKSPACE, AR_AGENT_DIR, AR_RULES, and
AR_OBJECTIVE. The agent owns its inner loop entirely: it may run
anything, delete anything, use git internally, or parallelize inside its
sandbox. Keep-versus-revert is agent policy; the kernel only records and
reports best-so-far.

Adding an agent touches nothing else: create agents/<name>/, write
agent.yaml plus whatever the command runs, and pass --agent <name>.

Bundled agents:
  dummy         scripted end-to-end test agent
  claude-code   Claude Code in a fresh-context loop; memory lives in the
                API history and a notes.md journal in the workspace
""",
    "runners": """\
A runner turns "give me a sandbox with these resources" into reality on
this machine. Four operations: build (cached environment image),
provision (a sandbox), exec (one command, used for official evals, one
fresh sandbox each), and spawn (the long-running agent).

  local    subprocess on the host. No isolation: the agent inherits your
           host environment and its restrictions, and setup commands run
           directly on the host. Instant startup; for development and
           experiments you trust.

  apple    Apple containers (github.com/apple/container). Each sandbox
           is a lightweight VM. Environment and agent layers are cached
           with `container build`. Sandboxes reach the kernel API at the
           vmnet gateway (default 192.168.64.1, override with
           AR_APPLE_GATEWAY). Requires `container system start` once per
           boot. The containerized default on macOS.

  docker   Docker runner with the same contract; layers are cached via
           run + commit; containers reach the API at host.docker.internal.

  apptainer  Apptainer/Singularity for Slurm clusters. No daemon, no
           root: environment images are fakeroot sandbox directories
           cached on shared storage, so one build serves all nodes.
           Host network is shared (API at 127.0.0.1). CPU/mem limits are
           left to the Slurm allocation, and net:false is not enforced.
           environment.base takes a registry image or a .def file.

Canonical paths inside every sandbox: /workspace (rw), /agent (ro),
/rules.md (ro), and for eval sandboxes /eval (ro) and /result (rw).
Container runners mount them literally; the local runner rewrites the
tokens in command strings and env values. This is what keeps an
experiment portable across runners.

Adding a runner is one module implementing the Runner contract plus a
registry entry; nothing above the runner changes.
""",
    "tracking": """\
Every submit is exactly one git commit in a kernel-owned repository.

The git-dir lives outside the sandbox and the work tree is the mounted
workspace, so there is no .git inside the sandbox: the agent cannot
tamper with history, and its own git usage cannot collide with the
kernel's.

Track by default, exclude by predicate:
  - paths starting with "." are ignored (the dot rule, at every level)
  - a small junk list (__pycache__/, node_modules/, venv/, wandb/, *.pyc)
    plus tracking.extra_ignores
  - files >= tracking.max_file_mb are recorded in a manifest as
    {path, size_mb, sha256}, bytes not stored. The manifest rides in the
    commit message and the submit record. Hashes are cached by size and
    mtime, so unchanged checkpoints are not re-read every submit.

Official eval materializes the live workspace, including large files, at
submit time: git is the history, not the transport. The manifest hash
attests that what eval consumed matches the snapshot.

Provenance of any attempt is two ids: environment image digest plus
commit hash. `ar diff` and the dashboard's files tab read this history;
`ar best | jq -r .commit` gives you the winning snapshot to check out.
""",
    "api": """\
The kernel HTTP API is the only channel between agent and kernel.
HTTP only, by design: anything that can curl can be an agent. The
address is injected as AR_API_URL.

  POST /submit          checkpoint the workspace. Body must match the
                        experiment's submit signature. Returns
                        {submit_id} immediately after the snapshot;
                        scoring is asynchronous. One submit at a time:
                        a submit while one is scoring returns 409,
                        never queues. 400 on a payload that violates
                        the signature.
  GET  /submit/{id}     the record; status is snapshotted, scored, or
                        failed. Failed carries the eval error so the
                        agent can fix and resubmit.
  GET  /history         all records. Test metrics are stripped, always.
  GET  /best            best submit so far (404 before the first score).
  GET  /experiment      objective, rules, submit signature, and the
                        remaining budget.
  GET  /health          liveness probe.

Submit is a deliberate checkpoint: quiesce first, submit, poll, read the
verdict, decide the next move. In official mode the agent never runs the
scorer and never sees test data. In reported mode the kernel still
snapshots on every submit, so a reported run can be re-verified later.
""",
}

ALIASES = {
    "agent": "agents", "runner": "runners", "concepts": None,
    "experiments": "experiment", "git": "tracking", "http": "api",
    "contract": "design",
}


def lookup(topic: str) -> str | None:
    topic = ALIASES.get(topic, topic)
    if topic is None:
        return None
    if topic == "design":
        from pathlib import Path
        try:
            return (Path(__file__).parent / "DESIGN.md").read_text()
        except OSError:
            return "(the bundled DESIGN.md is missing from this installation)"
    return COMMANDS.get(topic) or CONCEPTS.get(topic)

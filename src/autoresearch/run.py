"""The orchestrator: wires one run together and supervises it.

Owns the lifecycle: run directory, seed copies, trackers, environment
build, API server, agent launch, stop conditions, teardown. Everything it
does is recorded in events.jsonl, which together with the git repo is
sufficient to reconstruct the run.

Parallel agents: `--agent` may repeat (mixed agent types welcome). Each
instance gets its own workspace and its own git branch (agent 0 on main,
agent i on agent-i) in one shared object store, so cross-agent diffs
work. All agents share one submit lane and retry on 409; the best is
global. Agent i reaches the API at AR_API_URL, which carries an /a/<i>
prefix the server uses for attribution.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .events import EventLog
from .experiment import load_experiment
from .record import now_iso
from .registry import resolve_agent, resolve_runner
from .runners.base import Mount, SandboxSpec
from .scoring import Scorer
from .server import KernelServer
from .state import RunState
from .tracking import Tracker

POLL_SECONDS = 1.0


@dataclass
class RunResult:
    run_dir: Path
    reason: str
    submits: int
    best: dict | None


def _workspace_path(run_dir: Path, idx: int) -> Path:
    return run_dir / ("workspace" if idx == 0 else f"workspace_{idx}")


def start_run(
    experiment_path: str | Path,
    agent: str | list[str],
    runner_name: str = "local",
    run_name: str | None = None,
    runs_root: str | Path = "runs",
    resume: bool = False,
    quiet: bool = False,
    agent_env: dict[str, str] | None = None,
) -> RunResult:
    experiment = load_experiment(experiment_path)
    runner = resolve_runner(runner_name)
    agent_names = [agent] if isinstance(agent, str) else list(agent)
    agent_specs = [resolve_agent(name, Path.cwd()) for name in agent_names]
    labels = [f"{i}-{spec.name}" for i, spec in enumerate(agent_specs)]

    def say(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    runs_root = Path(runs_root).resolve()
    run_name = run_name or time.strftime("run_%Y%m%d_%H%M%S")
    run_dir = runs_root / run_name

    if resume:
        if not (run_dir / "events.jsonl").exists():
            raise RuntimeError(f"cannot resume: {run_dir} has no events.jsonl")
    else:
        if run_dir.exists():
            raise RuntimeError(f"run dir {run_dir} already exists (use --resume or a new --name)")
        run_dir.mkdir(parents=True)
        for i in range(len(agent_specs)):
            shutil.copytree(experiment.seed_dir, _workspace_path(run_dir, i))
        (run_dir / "rules.md").write_text(experiment.rules)
        (run_dir / "run.json").write_text(json.dumps({
            "schema": 1,
            "experiment": str(experiment.path),
            "experiment_name": experiment.name,
            "agent": agent_specs[0].name if len(agent_specs) == 1 else [s.name for s in agent_specs],
            "agents": labels,
            "runner": runner_name,
            "created": now_iso(),
            "objective": {
                "metric": experiment.objective.metric,
                "direction": experiment.objective.direction,
                "mode": experiment.objective.mode,
            },
            "stop": {
                "max_submits": experiment.stop.max_submits,
                "max_hours": experiment.stop.max_hours,
                "target": experiment.stop.target,
            },
        }, indent=2))

    trackers = []
    for i in range(len(agent_specs)):
        tracker = Tracker(
            git_dir=run_dir / "repo.git",
            work_tree=_workspace_path(run_dir, i),
            max_file_mb=experiment.tracking.max_file_mb,
            ignores=experiment.tracking.ignores,
            branch="main" if i == 0 else f"agent-{i}",
        )
        tracker.init()
        if not resume:
            tracker.snapshot("seed")
        trackers.append(tracker)

    say(f"run: {run_dir}")
    say(f"building environment ({runner_name}: {experiment.environment.base})...")
    image = runner.build(experiment.environment, experiment.path, run_dir.parent / ".cache")

    log = EventLog(run_dir / "events.jsonl")
    state = RunState(experiment, trackers, log, image, agent_labels=labels)
    if resume:
        state.load_existing()

    scorer = Scorer(runner, experiment, state, run_dir, image)
    state.score_async = scorer.schedule

    server = KernelServer(state, bind=runner.api_bind())
    server.start()
    api_url = f"http://{runner.api_host()}:{server.port}"
    log.append("run.started", schema=1, run=run_name, experiment=experiment.name,
               agent=", ".join(labels), runner=runner_name, image=image,
               api_url=api_url, resumed=resume)
    say(f"kernel API: {api_url}")

    sandboxes, processes = [], []
    for i, spec in enumerate(agent_specs):
        agent_image = runner.build_layer(image, spec.image_setup, cache_key=spec.name)
        sandbox = runner.provision(agent_image, SandboxSpec(
            mounts=[
                Mount(_workspace_path(run_dir, i), "/workspace", "rw"),
                Mount(spec.path, "/agent", "ro"),
                Mount(run_dir / "rules.md", "/rules.md", "ro"),
                *(Mount(Path(m["source"]).expanduser(), m["target"], m.get("mode", "rw"))
                  for m in spec.mounts),
            ],
            env={
                "AR_API_URL": f"{api_url}/a/{i}",
                "AR_AGENT_ID": str(i),
                "AR_WORKSPACE": "/workspace",
                "AR_AGENT_DIR": "/agent",
                "AR_RULES": "/rules.md",
                "PYTHONUNBUFFERED": "1",  # agent logs survive termination
                "AR_OBJECTIVE": json.dumps({
                    "metric": experiment.objective.metric,
                    "direction": experiment.objective.direction,
                    "mode": experiment.objective.mode,
                }),
                **spec.env,
                **(agent_env or {}),
            },
            resources=experiment.environment.resources,
            workdir="/workspace",
            name=f"agent{i}-{spec.name}",
        ))
        log_path = run_dir / ("agent.log" if i == 0 else f"agent_{i}.log")
        process = sandbox.spawn(spec.command, log_path)
        log.append("agent.started", agent=labels[i], command=spec.command)
        say(f"agent '{labels[i]}' started (log: {log_path})")
        sandboxes.append(sandbox)
        processes.append(process)

    exited: dict[int, int] = {}
    reason = "unknown"
    try:
        while True:
            time.sleep(POLL_SECONDS)
            stop = state.should_stop()
            if stop:
                reason = stop
                break
            for i, process in enumerate(processes):
                if i in exited:
                    continue
                code = process.poll()
                if code is not None:
                    exited[i] = code
                    log.append("agent.exited", agent=labels[i], code=code)
            if len(exited) == len(processes):
                codes = ", ".join(f"{labels[i]}: {exited[i]}" for i in sorted(exited))
                reason = f"all agents exited ({codes})" if len(processes) > 1 \
                    else f"agent exited with code {exited[0]}"
                break
    except KeyboardInterrupt:
        reason = "interrupted by user"
    finally:
        state.request_stop(reason)
        # Let an in-flight eval land; it is the score of work already done.
        grace = experiment.objective.timeout_seconds + 60
        waited = 0.0
        while state.scoring_in_flight and waited < grace:
            time.sleep(0.5)
            waited += 0.5
        for process in processes:
            process.terminate()
        for sandbox in sandboxes:
            sandbox.destroy()
        server.stop()
        log.append("run.finished", reason=reason,
                   submits=len(state.records), best=state.public_best())

    best = state.public_best()
    say(f"run finished: {reason}")
    if best:
        say(f"best: submit {best['submit_id']}  {best['metric']['name']}={best['metric']['value']}  commit {best['commit'][:8]}")
    return RunResult(run_dir=run_dir, reason=reason, submits=len(state.records), best=best)

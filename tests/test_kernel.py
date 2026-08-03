"""Kernel tests: schema validation, the submit transaction, tracking rules,
and the full end-to-end loop with the dummy agent on the local runner."""

import json
import subprocess
import textwrap
import threading
from pathlib import Path

import pytest

from autoresearch.events import EventLog
from autoresearch.experiment import ExperimentError, load_experiment
from autoresearch.record import SubmitRecord, now_iso
from autoresearch.run import start_run
from autoresearch.runners.base import Mount, SandboxSpec
from autoresearch.runners.local import LocalSandbox
from autoresearch.state import RunState
from autoresearch.tracking import Tracker

REPO = Path(__file__).resolve().parent.parent
TOY = REPO / "experiments" / "toy_quadratic"


# ── helpers ──────────────────────────────────────────────────────────

def write_experiment(root: Path, yaml_text: str, with_eval: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "experiment.yaml").write_text(textwrap.dedent(yaml_text))
    (root / "rules.md").write_text("# rules\n")
    (root / "seed").mkdir(exist_ok=True)
    (root / "seed" / "x.txt").write_text("seed\n")
    if with_eval:
        (root / "eval").mkdir(exist_ok=True)
        (root / "eval" / "run.py").write_text("print('eval')\n")
    return root


OFFICIAL_YAML = """
    name: t
    objective:
      metric: score
      direction: maximize
      mode: official
      eval_command: python3 /eval/run.py
      metric_path: metrics.json:score
    submit:
      signature:
        notes: {type: string, required: true}
    stop: {max_submits: 10}
"""

REPORTED_YAML = """
    name: t
    objective: {metric: score, direction: maximize, mode: reported}
    submit:
      signature:
        score: {type: number, required: true}
        notes: {type: string, required: false}
    stop: {max_submits: 10}
"""


def make_state(tmp_path: Path, yaml_text: str) -> RunState:
    exp_dir = write_experiment(tmp_path / "exp", yaml_text)
    experiment = load_experiment(exp_dir)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tracker = Tracker(tmp_path / "repo.git", workspace, 5.0, experiment.tracking.ignores)
    tracker.init()
    log = EventLog(tmp_path / "events.jsonl")
    return RunState(experiment, tracker, log, "test:img")


# ── schema validation ────────────────────────────────────────────────

def test_toy_experiment_validates():
    exp = load_experiment(TOY)
    assert exp.objective.metric == "val_loss"
    assert exp.objective.test_command is not None


def test_official_mode_requires_eval_harness(tmp_path):
    exp_dir = write_experiment(tmp_path / "exp", OFFICIAL_YAML, with_eval=False)
    with pytest.raises(ExperimentError, match="eval"):
        load_experiment(exp_dir)


def test_reported_mode_requires_metric_in_signature(tmp_path):
    bad = REPORTED_YAML.replace("score: {type: number, required: true}",
                                "other: {type: number, required: true}")
    exp_dir = write_experiment(tmp_path / "exp", bad)
    with pytest.raises(ExperimentError, match="self-reported"):
        load_experiment(exp_dir)


def test_unknown_top_level_key_rejected(tmp_path):
    exp_dir = write_experiment(tmp_path / "exp", OFFICIAL_YAML + "\n    objectve: {}")
    with pytest.raises(ExperimentError, match="unknown top-level key"):
        load_experiment(exp_dir)


def test_payload_validation(tmp_path):
    experiment = load_experiment(write_experiment(tmp_path / "exp", OFFICIAL_YAML))
    assert experiment.validate_payload({"notes": "ok"}) == []
    assert any("missing" in p for p in experiment.validate_payload({}))
    assert any("must be a string" in p for p in experiment.validate_payload({"notes": 3}))
    assert any("unknown field" in p for p in experiment.validate_payload({"notes": "x", "extra": 1}))
    # booleans are not numbers
    exp2 = load_experiment(write_experiment(tmp_path / "exp2", REPORTED_YAML))
    assert any("must be a number" in p for p in exp2.validate_payload({"score": True}))


# ── the submit transaction ───────────────────────────────────────────

def test_reported_submit_scores_immediately_and_tracks_best(tmp_path):
    state = make_state(tmp_path, REPORTED_YAML)
    body, status = state.submit({"score": 1.0})
    assert status == 200 and body["status"] == "scored"
    body, status = state.submit({"score": 3.0})
    assert status == 200
    body, status = state.submit({"score": 2.0})  # regression: best must not move
    assert status == 200
    best = state.public_best()
    assert best["submit_id"] == 2 and best["metric"]["value"] == 3.0
    assert state.records[2].best_so_far and not state.records[3].best_so_far


def test_official_submit_serialized(tmp_path):
    state = make_state(tmp_path, OFFICIAL_YAML)
    release = threading.Event()
    started = []
    state.score_async = lambda record: started.append(record)  # scoring never finishes

    body, status = state.submit({"notes": "first"})
    assert status == 200 and state.scoring_in_flight
    body, status = state.submit({"notes": "second"})
    assert status == 409, "concurrent submit must be rejected, not queued"

    state.finish_scoring(started[0], 5.0, None, None)
    assert not state.scoring_in_flight
    body, status = state.submit({"notes": "third"})
    assert status == 200
    release.set()


def test_test_metric_stripped_from_public_views(tmp_path):
    state = make_state(tmp_path, OFFICIAL_YAML)
    state.score_async = lambda record: None
    state.submit({"notes": "x"})
    record = state.records[1]
    state.finish_scoring(record, 5.0, 4.5, None)
    assert record.test_metric is not None
    assert "test_metric" not in state.public_record(1)
    assert all("test_metric" not in r for r in state.public_history())
    assert "test_metric" not in state.public_best()
    # but the event log keeps it
    logged = state.log.replay_records()[1]
    assert logged.test_metric.value == 4.5


def test_resume_marks_orphaned_submit_failed(tmp_path):
    state = make_state(tmp_path, OFFICIAL_YAML)
    state.score_async = lambda record: None
    state.submit({"notes": "x"})
    assert state.records[1].status == "snapshotted"
    # simulate a kernel restart on the same log
    state2 = RunState(state.experiment, state.trackers, state.log, "test:img")
    state2.load_existing()
    assert state2.records[1].status == "failed"
    assert state2.next_id == 2


def test_target_stop(tmp_path):
    state = make_state(tmp_path, REPORTED_YAML.replace(
        "stop: {max_submits: 10}", "stop: {max_submits: 10, target: 5}"))
    state.submit({"score": 4.0})
    assert state.should_stop() is None
    state.submit({"score": 5.5})
    assert "target" in (state.should_stop() or "")
    _, status = state.submit({"score": 6.0})
    assert status == 409, "no submits accepted while stopping"


# ── tracking rules ───────────────────────────────────────────────────

def test_tracking_rules(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "code.py").write_text("x = 1\n")
    (workspace / ".hidden").write_text("dot")
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__" / "a.pyc").write_bytes(b"x")
    (workspace / "big.bin").write_bytes(b"\x00" * (6 * 1024 * 1024))
    (workspace / "sub").mkdir()
    (workspace / "sub" / ".env").write_text("secret")
    (workspace / "sub" / "keep.txt").write_text("keep")

    tracker = Tracker(tmp_path / "repo.git", workspace, 5.0, ["__pycache__/"])
    tracker.init()
    commit, manifest = tracker.snapshot("submit 1")

    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        env={"GIT_DIR": str(tmp_path / "repo.git"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(tracked) == ["code.py", "sub/keep.txt"]
    assert [lf.path for lf in manifest] == ["big.bin"]
    assert manifest[0].size_mb == 6.0

    # unchanged large file: hash cache hit, manifest identical, empty commit still lands
    commit2, manifest2 = tracker.snapshot("submit 2")
    assert commit2 != commit
    assert manifest2[0].sha256 == manifest[0].sha256


def test_agent_git_usage_does_not_collide(tmp_path):
    """The agent may run git inside its workspace; kernel history is elsewhere."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "code.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    tracker = Tracker(tmp_path / "repo.git", workspace, 5.0, [])
    tracker.init()
    commit, _ = tracker.snapshot("submit 1")
    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        env={"GIT_DIR": str(tmp_path / "repo.git"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert tracked == ["code.py"], "the agent's .git must be invisible to kernel history"


# ── local runner token rewriting ─────────────────────────────────────

def test_token_rewrite_boundaries(tmp_path):
    sandbox = LocalSandbox(SandboxSpec(mounts=[
        Mount(tmp_path / "ws", "/workspace"),
        Mount(tmp_path / "ev", "/eval"),
        Mount(tmp_path / "res", "/result"),
    ]))
    out = sandbox._rewrite("python3 /eval/run.py --out /result/m.json")
    assert out == f"python3 {tmp_path}/ev/run.py --out {tmp_path}/res/m.json"
    # /eval must not match inside /evals; unknown tokens untouched
    assert sandbox._rewrite("/evals/00001/workspace") == "/evals/00001/workspace"
    # bare token at end of string
    assert sandbox._rewrite("/workspace") == str(tmp_path / "ws")


# ── end to end ───────────────────────────────────────────────────────

def test_random_search_agent_end_to_end(tmp_path):
    """A pure-python agent with no LLM: proves 'an agent is any process
    that edits the workspace and calls submit'."""
    result = start_run(
        TOY,
        agent=str(REPO / "agents" / "random-search"),
        runner_name="local",
        run_name="rs",
        runs_root=tmp_path,
        quiet=True,
    )
    assert result.reason == "max_submits 8 reached"
    assert result.submits == 8
    records = EventLog(result.run_dir / "events.jsonl").replay_records()
    baseline = records[1].metric.value
    assert result.best["metric"]["value"] < baseline, "search must improve on the seed"


def test_parallel_agents_share_one_run(tmp_path):
    """Two agents, one experiment: per-agent workspaces and branches, one
    shared submit lane and one global best."""
    rs = str(REPO / "agents" / "random-search")
    result = start_run(
        TOY,
        agent=[rs, rs],
        runner_name="local",
        run_name="par",
        runs_root=tmp_path,
        quiet=True,
    )
    records = EventLog(result.run_dir / "events.jsonl").replay_records()
    labels = {r.agent for r in records.values()}
    assert labels == {"0-random-search", "1-random-search"}, labels
    # per-agent branches in one shared object store
    branches = subprocess.run(
        ["git", "branch", "--list", "--format=%(refname:short)"],
        env={"GIT_DIR": str(result.run_dir / "repo.git"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "main" in branches and "agent-1" in branches
    # both workspaces exist and diverged from one seed
    assert (result.run_dir / "workspace").is_dir()
    assert (result.run_dir / "workspace_1").is_dir()
    assert result.best is not None


def test_end_to_end_toy_run(tmp_path):
    result = start_run(
        TOY,
        agent=str(REPO / "agents" / "dummy"),
        runner_name="local",
        run_name="e2e",
        runs_root=tmp_path,
        quiet=True,
    )
    assert result.reason == "agent exited with code 0"
    assert result.submits == 4
    assert result.best["submit_id"] == 3
    assert result.best["metric"]["value"] == 0.0

    records = EventLog(result.run_dir / "events.jsonl").replay_records()
    assert all(r.status == "scored" for r in records.values())
    # test metrics live in the log, and diverge from val
    assert records[3].test_metric.value == pytest.approx(0.0021667, abs=1e-4)
    # large file went to the manifest, not the tree
    assert [lf.path for lf in records[1].large_files] == ["data.bin"]
    # one commit per submit plus the seed commit
    log = subprocess.run(
        ["git", "log", "--format=%s"],
        env={"GIT_DIR": str(result.run_dir / "repo.git"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    assert [l for l in log if l] == ["submit 4", "submit 3", "submit 2", "submit 1", "seed"]
    # agent saw the 409 and the stripped API (asserted inside the agent itself)
    agent_log = (result.run_dir / "agent.log").read_text()
    assert "concurrent submit -> 409" in agent_log
    assert "final best: submit 3" in agent_log
    # the viz hook rendered an artifact per scored submit
    viz = result.run_dir / "evals" / "00003" / "viz" / "viz.svg"
    assert viz.is_file() and "<svg" in viz.read_text()

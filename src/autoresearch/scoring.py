"""Official scoring: hermetic evaluation of a snapshot.

The eval sandbox is built from the cached environment image and materializes
the workspace by live copy at submit time (git is the history, not the
transport, so large files flow through even though only their hashes are in
the snapshot). The agent never runs the scorer and never sees /eval.

Val and test run against the same materialized workspace, sequentially, each
with its own /result mount. Test results go only to the event log.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from .experiment import Experiment
from .record import SubmitRecord
from .runners.base import Mount, Runner, SandboxSpec
from .state import RunState


class Scorer:
    def __init__(self, runner: Runner, experiment: Experiment, state: RunState,
                 run_dir: Path, image: str):
        self.runner = runner
        self.experiment = experiment
        self.state = state
        self.run_dir = run_dir
        self.image = image

    def schedule(self, record: SubmitRecord) -> None:
        thread = threading.Thread(target=self._score, args=(record,), daemon=True, name=f"score-{record.submit_id}")
        thread.start()

    # ── internals ────────────────────────────────────────────────────

    def _score(self, record: SubmitRecord) -> None:
        eval_dir = self.run_dir / "evals" / f"{record.submit_id:05d}"
        try:
            value, test_value = self._run_evals(record, eval_dir)
            error = None
        except EvalFailure as e:
            value, test_value, error = None, None, str(e)
        except Exception as e:  # never let a scoring crash wedge the run
            value, test_value, error = None, None, f"scorer crashed: {e!r}"
        self.state.finish_scoring(record, value, test_value, error)
        if error is None:
            shutil.rmtree(eval_dir / "workspace", ignore_errors=True)

    def _run_evals(self, record: SubmitRecord, eval_dir: Path) -> tuple[float, float | None]:
        obj = self.experiment.objective
        workspace_copy = eval_dir / "workspace"
        if workspace_copy.exists():
            shutil.rmtree(workspace_copy)
        shutil.copytree(self.state.workspace_for(record), workspace_copy, symlinks=True)

        self.state.log.append("eval.started", submit_id=record.submit_id)
        value = self._run_one(obj.eval_command, workspace_copy, eval_dir / "result_val", "val")
        test_value = None
        if obj.test_command:
            test_value = self._run_one(obj.test_command, workspace_copy, eval_dir / "result_test", "test")
        if obj.viz_command:
            self._render_viz(workspace_copy, eval_dir / "viz")
        self.state.log.append("eval.finished", submit_id=record.submit_id)
        return value, test_value

    def _render_viz(self, workspace_copy: Path, viz_dir: Path) -> None:
        """Best-effort: a broken viz never fails a scored submit."""
        try:
            self._run_one_raw(self.experiment.objective.viz_command, workspace_copy, viz_dir, "viz")
        except Exception as e:
            (viz_dir / "viz_error.txt").parent.mkdir(parents=True, exist_ok=True)
            (viz_dir / "viz_error.txt").write_text(str(e))

    def _run_one(self, command: str, workspace_copy: Path, result_dir: Path, split: str) -> float:
        result = self._run_one_raw(command, workspace_copy, result_dir, split)
        return self._parse_metric(result_dir, split)

    def _run_one_raw(self, command: str, workspace_copy: Path, result_dir: Path, split: str):
        obj = self.experiment.objective
        result_dir.mkdir(parents=True, exist_ok=True)
        spec = SandboxSpec(
            mounts=[
                Mount(workspace_copy, "/workspace", "rw"),
                Mount(self.experiment.eval_dir, "/eval", "ro"),
                Mount(result_dir, "/result", "rw"),
            ],
            env={
                "AR_WORKSPACE": "/workspace",
                "AR_EVAL": "/eval",
                "AR_RESULT": "/result",
                "AR_SPLIT": split,
            },
            resources=self.experiment.environment.resources,
            workdir="/workspace",
            name=f"eval-{split}",
        )
        sandbox = self.runner.provision(self.image, spec)
        try:
            result = sandbox.exec(command, timeout=obj.timeout_seconds)
        finally:
            sandbox.destroy()

        log_path = result_dir.parent / f"eval_{split}.log"
        log_path.write_text(
            f"$ {command}\nexit={result.returncode} timed_out={result.timed_out}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
        )
        if result.timed_out:
            raise EvalFailure(f"{split} eval timed out after {obj.timeout_seconds}s")
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-8:]
            raise EvalFailure(f"{split} eval exited {result.returncode}: " + " | ".join(tail))
        return result

    def _parse_metric(self, result_dir: Path, split: str) -> float:
        obj = self.experiment.objective
        assert obj.metric_path is not None
        file_part, _, key_part = obj.metric_path.partition(":")
        metrics_file = result_dir / file_part
        if not metrics_file.is_file():
            raise EvalFailure(f"{split} eval did not write /result/{file_part}")
        try:
            data = json.loads(metrics_file.read_text())
        except json.JSONDecodeError as e:
            raise EvalFailure(f"/result/{file_part} is not valid JSON: {e}")
        value = data
        for key in key_part.split("."):
            if not isinstance(value, dict) or key not in value:
                raise EvalFailure(f"key '{key_part}' not found in /result/{file_part}")
            value = value[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvalFailure(f"metric '{key_part}' is not a number: {value!r}")
        return float(value)


class EvalFailure(Exception):
    pass

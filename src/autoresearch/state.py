"""RunState: the kernel's in-memory view of one run.

All mutation goes through this object under one lock. The submit transaction
lives here: validate, snapshot, record, then either finalize (reported mode)
or hand off to the scorer (official mode). Submit is serialized: a submit
while scoring is in flight is rejected, not queued.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .events import EventLog
from .experiment import Experiment
from .record import Metric, SubmitRecord, now_iso
from .tracking import Tracker


class RunState:
    def __init__(self, experiment: Experiment, tracker: Tracker | list[Tracker],
                 log: EventLog, image: str, agent_labels: list[str] | None = None):
        self.experiment = experiment
        self.trackers = [tracker] if isinstance(tracker, Tracker) else list(tracker)
        self.agent_labels = agent_labels or [""] * len(self.trackers)
        self.log = log
        self.image = image
        self.workspaces = [t.work_tree for t in self.trackers]
        self.lock = threading.RLock()
        self.records: dict[int, SubmitRecord] = {}
        self.next_id = 1
        self.scoring_in_flight = False
        self.stopping = False
        self.stop_reason: str | None = None
        self.started_monotonic = time.monotonic()
        # Set by the orchestrator: schedules official scoring off-thread.
        self.score_async: Callable[[SubmitRecord], None] | None = None
        self._best_id: int | None = None
        self._best_value: float | None = None

    # ── resume ───────────────────────────────────────────────────────

    def load_existing(self) -> None:
        self.records = self.log.replay_records()
        if self.records:
            self.next_id = max(self.records) + 1
        for rec in sorted(self.records.values(), key=lambda r: r.submit_id):
            if rec.status == "scored" and rec.metric is not None:
                self._consider_best(rec, mark=False)
            if rec.status == "snapshotted":
                # A crashed run left an unscored submit; mark it failed.
                rec.status = "failed"
                rec.error = "kernel restarted before scoring finished"
                self.log.submit_updated(rec)

    # ── the submit transaction ───────────────────────────────────────

    def submit(self, payload: object, agent_idx: int = 0) -> tuple[dict, int]:
        """Returns (response_json, http_status). One submit lane globally:
        parallel agents share it and retry on 409."""
        problems = self.experiment.validate_payload(payload)
        if problems:
            return {"error": "invalid payload", "problems": problems}, 400
        if not 0 <= agent_idx < len(self.trackers):
            return {"error": f"unknown agent index {agent_idx}"}, 404

        with self.lock:
            if self.stopping:
                return {"error": "run is stopping", "reason": self.stop_reason}, 409
            if self.scoring_in_flight:
                return {"error": "a submit is already being scored; poll it and retry"}, 409

            submit_id = self.next_id
            self.next_id += 1
            commit, manifest = self.trackers[agent_idx].snapshot(f"submit {submit_id}")
            record = SubmitRecord(
                submit_id=submit_id,
                time=now_iso(),
                commit=commit,
                image_digest=self.image,
                mode=self.experiment.objective.mode,
                status="snapshotted",
                agent=self.agent_labels[agent_idx],
                payload=dict(payload),  # type: ignore[arg-type]
                large_files=manifest,
            )
            self.records[submit_id] = record

            if self.experiment.objective.mode == "reported":
                value = float(payload[self.experiment.objective.metric])  # type: ignore[index]
                record.metric = Metric(self.experiment.objective.metric, value)
                record.status = "scored"
                self._consider_best(record)
                self.log.submit_updated(record)
                self._check_target(record)
                return {"submit_id": submit_id, "status": record.status}, 200

            self.log.submit_updated(record)
            self.scoring_in_flight = True

        assert self.score_async is not None, "orchestrator must install score_async"
        self.score_async(record)
        return {"submit_id": submit_id, "status": "snapshotted"}, 200

    def finish_scoring(
        self,
        record: SubmitRecord,
        value: float | None,
        test_value: float | None,
        error: str | None,
    ) -> None:
        metric_name = self.experiment.objective.metric
        with self.lock:
            if error is not None:
                record.status = "failed"
                record.error = error
            else:
                record.status = "scored"
                record.metric = Metric(metric_name, value)  # type: ignore[arg-type]
                if test_value is not None:
                    record.test_metric = Metric(metric_name, test_value)
                self._consider_best(record)
            self.log.submit_updated(record)
            self.scoring_in_flight = False
            if record.status == "scored":
                self._check_target(record)

    # ── best tracking ────────────────────────────────────────────────

    def _consider_best(self, record: SubmitRecord, mark: bool = True) -> None:
        assert record.metric is not None
        if self.experiment.objective.is_improvement(record.metric.value, self._best_value):
            self._best_id = record.submit_id
            self._best_value = record.metric.value
            if mark:
                record.best_so_far = True

    def _check_target(self, record: SubmitRecord) -> None:
        target = self.experiment.stop.target
        if target is None or record.metric is None:
            return
        obj = self.experiment.objective
        hit = record.metric.value <= target if obj.direction == "minimize" else record.metric.value >= target
        if hit:
            self.request_stop(f"target {target} reached at submit {record.submit_id}")

    def workspace_for(self, record: SubmitRecord):
        """The submitting agent's workspace (for eval materialization)."""
        label = record.agent or ""
        idx = int(label.split("-", 1)[0]) if label and label[0].isdigit() else 0
        return self.workspaces[idx] if 0 <= idx < len(self.workspaces) else self.workspaces[0]

    def request_stop(self, reason: str) -> None:
        with self.lock:
            if not self.stopping:
                self.stopping = True
                self.stop_reason = reason

    # ── agent-visible views (test metrics stripped) ──────────────────

    def public_record(self, submit_id: int) -> dict | None:
        rec = self.records.get(submit_id)
        return rec.to_public() if rec else None

    def public_history(self) -> list[dict]:
        return [self.records[i].to_public() for i in sorted(self.records)]

    def public_best(self) -> dict | None:
        if self._best_id is None:
            return None
        return self.records[self._best_id].to_public()

    def experiment_info(self) -> dict:
        exp = self.experiment
        elapsed_hours = (time.monotonic() - self.started_monotonic) / 3600
        return {
            "name": exp.name,
            "description": exp.description,
            "objective": {
                "metric": exp.objective.metric,
                "direction": exp.objective.direction,
                "mode": exp.objective.mode,
            },
            "submit_signature": [f.__dict__ for f in exp.signature],
            "budget": {
                "submits_used": len(self.records),
                "max_submits": exp.stop.max_submits,
                "hours_elapsed": round(elapsed_hours, 3),
                "max_hours": exp.stop.max_hours,
                "target": exp.stop.target,
            },
            "rules": exp.rules,
        }

    # ── stop conditions (polled by the orchestrator) ─────────────────

    def should_stop(self) -> str | None:
        with self.lock:
            if self.stopping:
                return self.stop_reason or "stop requested"
            stop = self.experiment.stop
            if stop.max_submits is not None and len(self.records) >= stop.max_submits:
                return f"max_submits {stop.max_submits} reached"
            if stop.max_hours is not None:
                if (time.monotonic() - self.started_monotonic) / 3600 >= stop.max_hours:
                    return f"max_hours {stop.max_hours} reached"
        return None

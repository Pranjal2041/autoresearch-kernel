"""Append-only JSONL event log: the single source of truth for a run.

Every state change is an event. The dashboard, `ark history`, and resume all
replay this file. Nothing else is authoritative.

Event types:
    run.started, run.finished
    agent.started, agent.exited
    submit.updated        carries the full record on every status transition
    eval.started, eval.finished
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .record import SubmitRecord, now_iso


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, type: str, **data) -> dict:
        event = {"type": type, "time": now_iso(), **data}
        line = json.dumps(event, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        return event

    def submit_updated(self, record: SubmitRecord) -> None:
        self.append("submit.updated", record=record.to_dict())

    def replay(self) -> list[dict]:
        if not self.path.exists():
            return []
        events = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # torn tail write from a crashed run
        return events

    def replay_records(self) -> dict[int, SubmitRecord]:
        """Latest state of every submit record, by id."""
        records: dict[int, SubmitRecord] = {}
        for event in self.replay():
            if event.get("type") == "submit.updated" and "record" in event:
                rec = SubmitRecord.from_dict(event["record"])
                records[rec.submit_id] = rec
        return records

"""Submit records: the unit of history.

One record per submit. Status flow: snapshotted -> scored | failed.
Reported-mode submits go straight to scored (the payload is the score).

The agent-visible view (`to_public`) strips test metrics. Only the event log
and the dashboard ever see them.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field


@dataclass
class Metric:
    name: str
    value: float


@dataclass
class LargeFile:
    path: str
    size_mb: float
    sha256: str


@dataclass
class SubmitRecord:
    submit_id: int
    time: str
    commit: str
    image_digest: str
    mode: str  # "official" | "reported"
    status: str  # "snapshotted" | "scored" | "failed"
    agent: str = ""  # "<index>-<agent name>"; "" on pre-parallel records
    payload: dict = field(default_factory=dict)
    metric: Metric | None = None
    test_metric: Metric | None = None
    large_files: list[LargeFile] = field(default_factory=list)
    best_so_far: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_public(self) -> dict:
        d = self.to_dict()
        d.pop("test_metric", None)
        return d

    @staticmethod
    def from_dict(d: dict) -> "SubmitRecord":
        d = dict(d)
        if d.get("metric"):
            d["metric"] = Metric(**d["metric"])
        if d.get("test_metric"):
            d["test_metric"] = Metric(**d["test_metric"])
        d["large_files"] = [LargeFile(**lf) for lf in d.get("large_files") or []]
        return SubmitRecord(**d)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

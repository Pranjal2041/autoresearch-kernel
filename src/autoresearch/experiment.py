"""Experiment loading and validation.

An experiment is a folder:
    experiment.yaml    objective, environment, tracking, budgets
    rules.md           free-form rules and context given to the agent
    seed/              initial workspace contents
    eval/              eval harness, never visible to the agent (official mode)

The schema here is the reference implementation of DESIGN.md. Validation is
strict: unknown top-level keys are errors, so typos fail loudly at `ar
validate` instead of silently at 2am during a run.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_JUNK_IGNORES = [
    "__pycache__/",
    "node_modules/",
    "venv/",
    "wandb/",
    "*.pyc",
    "*.egg-info/",
]

SIGNATURE_TYPES = {"string", "number", "boolean"}


class ExperimentError(Exception):
    """Raised when an experiment folder fails validation."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("invalid experiment:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass
class Objective:
    metric: str
    direction: str  # "minimize" | "maximize"
    mode: str  # "official" | "reported"
    eval_command: str | None = None
    metric_path: str | None = None  # "metrics.json:val_loss"
    timeout_seconds: int = 900
    test_command: str | None = None
    viz_command: str | None = None  # optional: renders /result/viz.{svg,png,html}

    def is_improvement(self, new: float, old: float | None) -> bool:
        if old is None:
            return True
        return new < old if self.direction == "minimize" else new > old


@dataclass
class SignatureField:
    name: str
    type: str  # "string" | "number" | "boolean"
    required: bool = True

    def accepts(self, value: object) -> bool:
        if self.type == "string":
            return isinstance(value, str)
        if self.type == "number":
            # bool is an int subclass; a boolean is not a number here.
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if self.type == "boolean":
            return isinstance(value, bool)
        return False


@dataclass
class Environment:
    base: str = "python:3.12-slim"
    resources: dict = field(default_factory=lambda: {"cpu": 2, "mem_gb": 4, "gpu": 0, "net": True})
    setup: list[str] = field(default_factory=list)


@dataclass
class Tracking:
    max_file_mb: float = 5.0
    extra_ignores: list[str] = field(default_factory=list)

    @property
    def ignores(self) -> list[str]:
        return DEFAULT_JUNK_IGNORES + self.extra_ignores


@dataclass
class Stop:
    max_submits: int | None = None
    max_hours: float | None = None
    target: float | None = None


@dataclass
class Experiment:
    path: Path
    name: str
    description: str
    objective: Objective
    signature: list[SignatureField]
    environment: Environment
    tracking: Tracking
    stop: Stop
    rules: str

    @property
    def seed_dir(self) -> Path:
        return self.path / "seed"

    @property
    def eval_dir(self) -> Path:
        return self.path / "eval"

    def signature_field(self, name: str) -> SignatureField | None:
        for f in self.signature:
            if f.name == name:
                return f
        return None

    def validate_payload(self, payload: object) -> list[str]:
        """Check a submit payload against the experiment's signature."""
        problems: list[str] = []
        if not isinstance(payload, dict):
            return ["payload must be a JSON object"]
        for f in self.signature:
            if f.name not in payload:
                if f.required:
                    problems.append(f"missing required field '{f.name}'")
                continue
            if not f.accepts(payload[f.name]):
                problems.append(f"field '{f.name}' must be a {f.type}")
        for key in payload:
            if self.signature_field(key) is None:
                problems.append(f"unknown field '{key}' not in submit signature")
        return problems


TOP_LEVEL_KEYS = {
    "name",
    "description",
    "objective",
    "submit",
    "environment",
    "tracking",
    "stop",
}


def _parse_signature(raw: object, problems: list[str]) -> list[SignatureField]:
    fields: list[SignatureField] = []
    if raw is None:
        return fields
    if not isinstance(raw, dict):
        problems.append("submit.signature must be a mapping of field name to spec")
        return fields
    for name, spec in raw.items():
        if isinstance(spec, str):
            spec = {"type": spec}
        if not isinstance(spec, dict) or "type" not in spec:
            problems.append(f"submit.signature.{name}: expected {{type: ..., required: ...}}")
            continue
        ftype = spec["type"]
        if ftype not in SIGNATURE_TYPES:
            problems.append(f"submit.signature.{name}: type must be one of {sorted(SIGNATURE_TYPES)}")
            continue
        fields.append(SignatureField(name=name, type=ftype, required=bool(spec.get("required", True))))
    return fields


def load_experiment(path: str | Path) -> Experiment:
    path = Path(path).resolve()
    problems: list[str] = []

    yaml_path = path / "experiment.yaml"
    if not yaml_path.is_file():
        raise ExperimentError([f"no experiment.yaml in {path}"])
    try:
        raw = yaml.safe_load(yaml_path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ExperimentError([f"experiment.yaml is not valid YAML: {e}"])
    if not isinstance(raw, dict):
        raise ExperimentError(["experiment.yaml must be a mapping"])

    for key in raw:
        if key not in TOP_LEVEL_KEYS:
            problems.append(f"unknown top-level key '{key}'")

    name = raw.get("name") or path.name
    description = raw.get("description", "")

    # objective
    obj_raw = raw.get("objective")
    if not isinstance(obj_raw, dict):
        problems.append("objective section is required")
        obj_raw = {}
    metric = obj_raw.get("metric")
    if not metric:
        problems.append("objective.metric is required")
    direction = obj_raw.get("direction", "minimize")
    if direction not in ("minimize", "maximize"):
        problems.append("objective.direction must be minimize or maximize")
    mode = obj_raw.get("mode", "official")
    if mode not in ("official", "reported"):
        problems.append("objective.mode must be official or reported")
    objective = Objective(
        metric=metric or "metric",
        direction=direction,
        mode=mode,
        eval_command=obj_raw.get("eval_command"),
        metric_path=obj_raw.get("metric_path"),
        timeout_seconds=int(obj_raw.get("timeout_seconds", 900)),
        test_command=obj_raw.get("test_command"),
        viz_command=obj_raw.get("viz_command"),
    )

    signature = _parse_signature((raw.get("submit") or {}).get("signature"), problems)

    if mode == "official":
        if not objective.eval_command:
            problems.append("official mode requires objective.eval_command")
        if not objective.metric_path:
            problems.append("official mode requires objective.metric_path (file.json:key)")
        if objective.metric_path and ":" not in objective.metric_path:
            problems.append("objective.metric_path must look like 'metrics.json:val_loss'")
        if not (path / "eval").is_dir():
            problems.append("official mode requires an eval/ folder")
    else:  # reported
        f = None
        for sf in signature:
            if sf.name == objective.metric:
                f = sf
        if f is None or f.type != "number" or not f.required:
            problems.append(
                f"reported mode requires submit.signature to include a required "
                f"number field named '{objective.metric}' (the self-reported metric)"
            )
        if objective.test_command:
            problems.append("reported mode cannot have a test_command (there is no official eval)")
        if objective.viz_command:
            problems.append("reported mode cannot have a viz_command (there is no eval sandbox)")

    # environment
    env_raw = raw.get("environment") or {}
    resources = {"cpu": 2, "mem_gb": 4, "gpu": 0, "net": True}
    resources.update(env_raw.get("resources") or {})
    setup = env_raw.get("setup") or []
    if not isinstance(setup, list) or not all(isinstance(s, str) for s in setup):
        problems.append("environment.setup must be a list of shell commands")
        setup = []
    environment = Environment(
        base=env_raw.get("base", "python:3.12-slim"),
        resources=resources,
        setup=setup,
    )

    # tracking
    tr_raw = raw.get("tracking") or {}
    tracking = Tracking(
        max_file_mb=float(tr_raw.get("max_file_mb", 5.0)),
        extra_ignores=list(tr_raw.get("extra_ignores") or []),
    )

    # stop
    st_raw = raw.get("stop") or {}
    stop = Stop(
        max_submits=st_raw.get("max_submits"),
        max_hours=st_raw.get("max_hours"),
        target=st_raw.get("target"),
    )
    if stop.max_submits is None and stop.max_hours is None:
        problems.append("stop must set max_submits or max_hours (unbounded runs must be explicit: use a large value)")

    # folder contents
    if not (path / "seed").is_dir():
        problems.append("missing seed/ folder (the initial workspace)")
    rules_path = path / "rules.md"
    if not rules_path.is_file():
        problems.append("missing rules.md")
        rules = ""
    else:
        rules = rules_path.read_text()

    for cmd_name in ("eval_command", "test_command", "viz_command"):
        cmd = getattr(objective, cmd_name)
        if cmd:
            try:
                shlex.split(cmd)
            except ValueError as e:
                problems.append(f"objective.{cmd_name} is not parseable: {e}")

    if problems:
        raise ExperimentError(problems)

    return Experiment(
        path=path,
        name=name,
        description=description,
        objective=objective,
        signature=signature,
        environment=environment,
        tracking=tracking,
        stop=stop,
        rules=rules,
    )

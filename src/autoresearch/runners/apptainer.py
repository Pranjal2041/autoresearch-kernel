"""Apptainer (Singularity) runner for Slurm clusters and shared Linux hosts.

Design notes, and how this differs from the container runners:

- No daemon and no root. Environment images are built as writable sandbox
  directories with --fakeroot: pull the base from a registry
  (docker://python:3.12-slim), run the setup hooks inside with
  --writable, and reuse the directory keyed by the same content hash as
  every other runner. The cache lives under the runs directory, which on
  a cluster is typically shared storage, so one build serves all nodes.

- Sandboxes share the host network (apptainer's default), so the kernel
  API is plain 127.0.0.1. Network isolation (`resources.net: false`) is
  NOT enforced: --net requires privileges most clusters do not grant.

- CPU and memory limits are NOT enforced by the runner: on Slurm the
  allocation already constrains the job, and that is the right layer for
  it. GPU access is passed through with --nv when resources.gpu > 0.

- `environment.base` may be a registry image name (docker:// is added if
  missing) or a path to an Apptainer .def file inside the experiment.
  Dockerfiles are not supported here; keep container builds for the
  docker/apple runners.

Typical Slurm use:
  salloc/srun an allocation, then inside it:
    ark run experiments/my_exp --agent claude-code --runner apptainer
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from ..experiment import Environment
from .base import ExecResult, Runner, Sandbox, SandboxSpec
from .local import LocalProcess


def _apptainer() -> str:
    for name in ("apptainer", "singularity"):
        if shutil.which(name):
            return name
    raise RuntimeError("neither 'apptainer' nor 'singularity' is on PATH")


def build_exec_args(binary: str, image: str, spec: SandboxSpec, extra_flags: list[str]) -> list[str]:
    """Pure argument construction, unit-testable without apptainer installed."""
    args = [binary, "exec", "--containall", "--cleanenv", "--pwd", spec.workdir]
    for mount in spec.mounts:
        suffix = ":ro" if mount.mode == "ro" else ""
        args += ["--bind", f"{Path(mount.source).resolve()}:{mount.target}{suffix}"]
    for key, value in spec.env.items():
        args += ["--env", f"{key}={value}"]
    if (spec.resources or {}).get("gpu"):
        args += ["--nv"]
    args += extra_flags
    args.append(image)
    return args


class ApptainerSandbox(Sandbox):
    def __init__(self, binary: str, image: str, spec: SandboxSpec):
        self.binary = binary
        self.image = image
        self.spec = spec

    def _command(self, command: str) -> list[str]:
        return [*build_exec_args(self.binary, self.image, self.spec, []),
                "sh", "-lc", command]

    def exec(self, command: str, timeout: float | None = None) -> ExecResult:
        try:
            result = subprocess.run(self._command(command), capture_output=True,
                                    text=True, timeout=timeout)
            return ExecResult(result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            return ExecResult(-1, out, err, timed_out=True)

    def spawn(self, command: str, log_path: Path) -> LocalProcess:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")
        popen = subprocess.Popen(
            self._command(command),
            stdout=log_file, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return LocalProcess(popen, log_file)

    def destroy(self) -> None:
        pass  # exec'd processes die with their process group; no daemon state


class ApptainerRunner(Runner):
    name = "apptainer"

    def __init__(self):
        self._binary: str | None = None

    @property
    def binary(self) -> str:
        if self._binary is None:
            self._binary = _apptainer()
        return self._binary

    def api_host(self) -> str:
        return "127.0.0.1"  # host network is shared

    def api_bind(self) -> str:
        return "127.0.0.1"

    # ── image building ───────────────────────────────────────────────

    def build(self, environment: Environment, experiment_dir: Path, cache_dir: Path) -> str:
        base = self._resolve_base(environment.base, experiment_dir)
        spec_hash = hashlib.sha256(json.dumps(
            {"base": base, "setup": environment.setup}, sort_keys=True).encode()).hexdigest()[:12]
        sandbox_dir = cache_dir / f"apptainer-env-{spec_hash}"
        marker = sandbox_dir / ".ar-build-done"
        if marker.exists():
            return str(sandbox_dir)
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)  # half-finished build
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._run(["build", "--force", "--fakeroot", "--sandbox", str(sandbox_dir), base],
                  timeout=3600, what=f"base build from {base}")
        for command in environment.setup:
            self._run(["exec", "--fakeroot", "--writable",
                       "--bind", f"{(experiment_dir / 'seed').resolve()}:/workspace:ro",
                       *(["--bind", f"{(experiment_dir / 'eval').resolve()}:/eval:ro"]
                         if (experiment_dir / "eval").is_dir() else []),
                       str(sandbox_dir), "sh", "-lc", command],
                      timeout=1800, what=f"setup: {command}")
        marker.touch()
        return str(sandbox_dir)

    def build_layer(self, image: str, commands: list[str], cache_key: str) -> str:
        if not commands:
            return image
        layer_hash = hashlib.sha256(json.dumps(
            {"image": image, "commands": commands, "key": cache_key},
            sort_keys=True).encode()).hexdigest()[:12]
        layer_dir = Path(image).parent / f"apptainer-layer-{layer_hash}"
        marker = layer_dir / ".ar-build-done"
        if marker.exists():
            return str(layer_dir)
        if layer_dir.exists():
            shutil.rmtree(layer_dir)
        # copy-on-build: clone the env sandbox dir, then run the layer commands
        shutil.copytree(image, layer_dir, symlinks=True)
        for command in commands:
            self._run(["exec", "--fakeroot", "--writable", str(layer_dir),
                       "sh", "-lc", command], timeout=1800, what=f"agent layer: {command}")
        marker.touch()
        return str(layer_dir)

    def provision(self, image: str, spec: SandboxSpec) -> Sandbox:
        return ApptainerSandbox(self.binary, image, spec)

    # ── internals ────────────────────────────────────────────────────

    def _resolve_base(self, base: str, experiment_dir: Path) -> str:
        def_file = experiment_dir / base
        if base.endswith(".def") and def_file.is_file():
            return str(def_file)
        if "://" in base:
            return base
        if base.endswith("Dockerfile") or "/Dockerfile" in base:
            raise RuntimeError(
                "the apptainer runner cannot build Dockerfiles; use a registry "
                "image (docker://...) or an Apptainer .def file as environment.base"
            )
        return f"docker://{base}"

    def _run(self, args: list[str], timeout: int, what: str) -> None:
        result = subprocess.run([self.binary, *args], capture_output=True, text=True,
                                timeout=timeout, env={**os.environ})
        if result.returncode != 0:
            raise RuntimeError(f"apptainer {what} failed:\n{result.stdout}\n{result.stderr}")

"""Docker runner.

Environment images are cached gym-anything style: run the setup hooks once in
a container built from the base, `docker commit` the result, and key the tag
by a content hash of the environment spec. Same for agent layers
(image_setup), keyed additionally by the agent's commands.

Sandboxes here are configurations, not containers: every exec() is its own
`docker run --rm` (one fresh container per eval, hermetic by construction),
and spawn() is a detached container supervised through docker inspect.

Canonical paths are realized as real mounts, so no token rewriting happens.
The kernel API binds 0.0.0.0 and containers reach it at host.docker.internal
(mapped to the host gateway on Linux via --add-host).
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
import uuid
from pathlib import Path

from ..experiment import Environment
from .base import ExecResult, Process, Runner, Sandbox, SandboxSpec

HOST_GATEWAY = "host.docker.internal"


def _docker(*args: str, timeout: float | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout, check=check)


def _image_exists(tag: str) -> bool:
    return _docker("image", "inspect", tag, check=False).returncode == 0


class DockerProcess(Process):
    def __init__(self, container: str):
        self.container = container

    def poll(self) -> int | None:
        result = _docker("inspect", "--format", "{{.State.Running}} {{.State.ExitCode}}",
                         self.container, check=False)
        if result.returncode != 0:
            return -1  # container vanished
        running, exit_code = result.stdout.split()
        return None if running == "true" else int(exit_code)

    def terminate(self, grace_seconds: float = 10.0) -> None:
        _docker("stop", "-t", str(int(grace_seconds)), self.container, check=False)


class DockerSandbox(Sandbox):
    def __init__(self, image: str, spec: SandboxSpec):
        self.image = image
        self.spec = spec
        self._spawned: list[str] = []

    def _run_args(self, network: bool) -> list[str]:
        args = []
        for mount in self.spec.mounts:
            suffix = ":ro" if mount.mode == "ro" else ""
            args += ["-v", f"{mount.source.resolve()}:{mount.target}{suffix}"]
        for key, value in self.spec.env.items():
            args += ["-e", f"{key}={value}"]
        resources = self.spec.resources or {}
        if resources.get("cpu"):
            args += [f"--cpus={resources['cpu']}"]
        if resources.get("mem_gb"):
            args += [f"--memory={resources['mem_gb']}g"]
        if resources.get("gpu"):
            args += ["--gpus", "all"]
        if network:
            args += ["--add-host", f"{HOST_GATEWAY}:host-gateway"]
        else:
            args += ["--network", "none"]
        args += ["--workdir", self.spec.workdir]
        return args

    def exec(self, command: str, timeout: float | None = None) -> ExecResult:
        # One fresh container per exec; eval hermeticity comes free.
        name = f"ar-exec-{uuid.uuid4().hex[:12]}"
        net = bool((self.spec.resources or {}).get("net", True))
        args = ["run", "--rm", "--name", name, *self._run_args(network=net),
                self.image, "sh", "-lc", command]
        try:
            result = _docker(*args, timeout=timeout, check=False)
            return ExecResult(result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired as e:
            _docker("rm", "-f", name, check=False)
            return ExecResult(-1, str(e.stdout or ""), str(e.stderr or ""), timed_out=True)

    def spawn(self, command: str, log_path: Path) -> Process:
        # The agent always gets a network: the kernel API is its lifeline.
        name = f"ar-{self.spec.name}-{uuid.uuid4().hex[:8]}"
        _docker("run", "-d", "--name", name, *self._run_args(network=True),
                self.image, "sh", "-lc", command)
        self._spawned.append(name)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Stream container output to the log file from the host side.
        with open(log_path, "a", encoding="utf-8") as log_file:
            subprocess.Popen(["docker", "logs", "-f", name],
                             stdout=log_file, stderr=subprocess.STDOUT)
        return DockerProcess(name)

    def destroy(self) -> None:
        for name in self._spawned:
            _docker("rm", "-f", name, check=False)


class DockerRunner(Runner):
    name = "docker"

    def api_host(self) -> str:
        return HOST_GATEWAY

    def api_bind(self) -> str:
        return "0.0.0.0"

    # ── image building ───────────────────────────────────────────────

    def build(self, environment: Environment, experiment_dir: Path, cache_dir: Path) -> str:
        base = self._resolve_base(environment.base, experiment_dir)
        if not environment.setup:
            return base
        spec_hash = hashlib.sha256(json.dumps(
            {"base": base, "setup": environment.setup}, sort_keys=True).encode()).hexdigest()[:12]
        tag = f"ar-env:{spec_hash}"
        if _image_exists(tag):
            return tag
        self._commit_layer(
            base, tag, environment.setup,
            mounts=[
                ("-v", f"{(experiment_dir / 'seed').resolve()}:/workspace:ro"),
                *([("-v", f"{(experiment_dir / 'eval').resolve()}:/eval:ro")]
                  if (experiment_dir / "eval").is_dir() else []),
            ],
        )
        return tag

    def build_layer(self, image: str, commands: list[str], cache_key: str) -> str:
        """Extra image layer (e.g. an agent's CLI installs), cached."""
        if not commands:
            return image
        layer_hash = hashlib.sha256(json.dumps(
            {"image": image, "commands": commands, "key": cache_key},
            sort_keys=True).encode()).hexdigest()[:12]
        tag = f"ar-layer:{layer_hash}"
        if not _image_exists(tag):
            self._commit_layer(image, tag, commands, mounts=[])
        return tag

    def _resolve_base(self, base: str, experiment_dir: Path) -> str:
        dockerfile = experiment_dir / base
        if dockerfile.is_file():  # base may be a Dockerfile path inside the experiment
            tag = f"ar-base:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
            if not _image_exists(tag):
                _docker("build", "-t", tag, "-f", str(dockerfile), str(experiment_dir),
                        timeout=3600)
            return tag
        return base  # a registry image name

    def _commit_layer(self, base: str, tag: str, commands: list[str], mounts: list[tuple]) -> None:
        name = f"ar-build-{uuid.uuid4().hex[:12]}"
        flat_mounts = [part for mount in mounts for part in mount]
        _docker("run", "-d", "--name", name, *flat_mounts, base, "sleep", "infinity",
                timeout=600)
        try:
            for command in commands:
                result = _docker("exec", name, "sh", "-lc", command,
                                 timeout=1800, check=False)
                if result.returncode != 0:
                    raise RuntimeError(
                        f"image setup failed in {base}: {command}\n"
                        f"{result.stdout}\n{result.stderr}"
                    )
            _docker("commit", name, tag, timeout=600)
        finally:
            _docker("rm", "-f", name, check=False)

    # ── sandboxes ────────────────────────────────────────────────────

    def provision(self, image: str, spec: SandboxSpec) -> Sandbox:
        return DockerSandbox(image, spec)

    def interactive_shell(self, image, mounts) -> int:
        args = ["docker", "run", "-it", "--rm"]
        for mount in mounts:
            args += ["-v", f"{mount.source.resolve()}:{mount.target}"]
        return subprocess.call([*args, image, "bash"])

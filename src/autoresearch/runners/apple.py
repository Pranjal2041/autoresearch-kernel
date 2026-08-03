"""Apple container runner (github.com/apple/container): the primary
containerized backend on macOS. Each container is a lightweight VM.

Differences from the Docker runner, both invisible above the Runner contract:
- No `commit`, so environment and agent layers are cached with
  `container build` over a generated Dockerfile (seed and eval are copied
  into the build context so setup hooks can reference /workspace and /eval).
- No host-gateway alias, so sandboxes reach the kernel API at the vmnet
  gateway IP (host side of the container subnet), default 192.168.64.1,
  overridable with AR_APPLE_GATEWAY.

Requires `container system start` once per boot.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from ..experiment import Environment
from .base import ExecResult, Process, Runner, Sandbox, SandboxSpec

DEFAULT_GATEWAY = "192.168.64.1"
INTERNAL_NETWORK = "ar-internal"  # host-only: enforces resources.net=false for evals

_internal_network_ready = False


def _ensure_internal_network() -> bool:
    global _internal_network_ready
    if _internal_network_ready:
        return True
    result = _container("network", "create", "--internal", INTERNAL_NETWORK, check=False)
    ok = result.returncode == 0 or "exist" in (result.stderr + result.stdout).lower()
    _internal_network_ready = ok
    return ok


def _container(*args: str, timeout: float | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["container", *args], capture_output=True, text=True,
                          timeout=timeout, check=check)


def _image_exists(tag: str) -> bool:
    result = _container("image", "list", "--format", "json", check=False)
    if result.returncode != 0:
        return False
    try:
        images = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False
    for image in images:
        # container 1.1.0: the tag lives in an OCI annotation, not a field.
        annotations = (image.get("configuration", {})
                       .get("descriptor", {})
                       .get("annotations", {}))
        name = annotations.get("com.apple.containerization.image.name", "")
        if name == tag or name.endswith(f"/{tag}"):
            return True
    return False


class AppleProcess(Process):
    def __init__(self, name: str):
        self.name = name

    def poll(self) -> int | None:
        result = _container("inspect", self.name, check=False)
        if result.returncode != 0:
            return -1  # container vanished
        try:
            info = json.loads(result.stdout)[0]
        except (json.JSONDecodeError, IndexError):
            return -1
        # container 1.1.0 shape: {"status": {"state": "running"|"stopped", ...}};
        # keep a string fallback in case the schema moves again.
        status = info.get("status")
        state = status.get("state") if isinstance(status, dict) else status
        if state == "running":
            return None
        return 0  # the CLI does not expose an exit code; stopped is all we know

    def terminate(self, grace_seconds: float = 10.0) -> None:
        _container("stop", "-t", str(int(grace_seconds)), self.name, check=False)


class AppleSandbox(Sandbox):
    def __init__(self, image: str, spec: SandboxSpec):
        self.image = image
        self.spec = spec
        self._spawned: list[tuple[str, Path]] = []

    def _run_args(self) -> list[str]:
        args = []
        for mount in self.spec.mounts:
            suffix = ":ro" if mount.mode == "ro" else ""
            args += ["-v", f"{mount.source.resolve()}:{mount.target}{suffix}"]
        for key, value in self.spec.env.items():
            args += ["-e", f"{key}={value}"]
        resources = self.spec.resources or {}
        if resources.get("cpu"):
            args += ["--cpus", str(resources["cpu"])]
        if resources.get("mem_gb"):
            args += ["--memory", f"{resources['mem_gb']}G"]
        args += ["--workdir", self.spec.workdir]
        return args

    def exec(self, command: str, timeout: float | None = None) -> ExecResult:
        name = f"ar-exec-{uuid.uuid4().hex[:12]}"
        # net:false is enforced for one-shot (eval) sandboxes via a host-only
        # network: no internet, so an eval cannot download answers or leak.
        net_args = []
        if not (self.spec.resources or {}).get("net", True) and _ensure_internal_network():
            net_args = ["--network", INTERNAL_NETWORK]
        try:
            result = _container("run", "--rm", "--name", name, *net_args, *self._run_args(),
                                self.image, "sh", "-lc", command,
                                timeout=timeout, check=False)
            return ExecResult(result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired as e:
            _container("delete", "-f", name, check=False)
            return ExecResult(-1, str(e.stdout or ""), str(e.stderr or ""), timed_out=True)

    def spawn(self, command: str, log_path: Path) -> Process:
        name = f"ar-{self.spec.name}-{uuid.uuid4().hex[:8]}"
        _container("run", "-d", "--name", name, *self._run_args(),
                   self.image, "sh", "-lc", command)
        self._spawned.append((name, log_path))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return AppleProcess(name)

    def destroy(self) -> None:
        for name, log_path in self._spawned:
            # `container logs` is unrecoverable once the container is gone,
            # and the follow variant proved unreliable; dump before deleting.
            result = _container("logs", name, check=False)
            if result.stdout or result.stderr:
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(result.stdout + result.stderr)
            _container("delete", "-f", name, check=False)


class AppleRunner(Runner):
    name = "apple"

    def api_host(self) -> str:
        return os.environ.get("AR_APPLE_GATEWAY", DEFAULT_GATEWAY)

    def api_bind(self) -> str:
        return "0.0.0.0"

    def build(self, environment: Environment, experiment_dir: Path, cache_dir: Path) -> str:
        self._check_system()
        base = self._resolve_base(environment.base, experiment_dir)
        if not environment.setup:
            return base
        spec_hash = hashlib.sha256(json.dumps(
            {"base": base, "setup": environment.setup}, sort_keys=True).encode()).hexdigest()[:12]
        tag = f"ar-env:{spec_hash}"
        if _image_exists(tag):
            return tag
        context = cache_dir / f"ctx-{spec_hash}"
        if context.exists():
            shutil.rmtree(context)
        context.mkdir(parents=True)
        shutil.copytree(experiment_dir / "seed", context / "seed")
        lines = [f"FROM {base}", "COPY seed /workspace"]
        if (experiment_dir / "eval").is_dir():
            shutil.copytree(experiment_dir / "eval", context / "eval")
            lines.append("COPY eval /eval")
        lines += [f"RUN {command}" for command in environment.setup]
        (context / "Dockerfile").write_text("\n".join(lines) + "\n")
        self._build(tag, context)
        return tag

    def build_layer(self, image: str, commands: list[str], cache_key: str) -> str:
        if not commands:
            return image
        layer_hash = hashlib.sha256(json.dumps(
            {"image": image, "commands": commands, "key": cache_key},
            sort_keys=True).encode()).hexdigest()[:12]
        tag = f"ar-layer:{layer_hash}"
        if not _image_exists(tag):
            context = Path("/tmp") / f"ar-layer-{layer_hash}"
            if context.exists():
                shutil.rmtree(context)
            context.mkdir(parents=True)
            lines = [f"FROM {image}"] + [f"RUN {command}" for command in commands]
            (context / "Dockerfile").write_text("\n".join(lines) + "\n")
            self._build(tag, context)
        return tag

    def provision(self, image: str, spec: SandboxSpec) -> Sandbox:
        return AppleSandbox(image, spec)

    def interactive_shell(self, image, mounts) -> int:
        args = ["container", "run", "-it", "--rm"]
        for mount in mounts:
            args += ["-v", f"{mount.source.resolve()}:{mount.target}"]
        return subprocess.call([*args, image, "bash"])

    # ── internals ────────────────────────────────────────────────────

    def _check_system(self) -> None:
        result = _container("system", "status", check=False)
        if result.returncode != 0 or "running" not in result.stdout:
            raise RuntimeError(
                "the Apple container system service is not running; "
                "start it with: container system start"
            )

    def _resolve_base(self, base: str, experiment_dir: Path) -> str:
        dockerfile = experiment_dir / base
        if dockerfile.is_file():
            tag = f"ar-base:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
            if not _image_exists(tag):
                result = _container("build", "-t", tag, "-f", str(dockerfile),
                                    str(experiment_dir), timeout=3600, check=False)
                if result.returncode != 0:
                    raise RuntimeError(f"base image build failed:\n{result.stdout}\n{result.stderr}")
            return tag
        return base

    def _build(self, tag: str, context: Path) -> None:
        result = _container("build", "-t", tag, str(context), timeout=3600, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"image build failed for {tag}:\n{result.stdout}\n{result.stderr}")

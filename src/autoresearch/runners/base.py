"""The Runner contract.

A runner turns "give me a sandbox with these resources" into reality on this
machine. Four operations: build, provision, exec (sync or spawned), destroy.

Canonical paths inside every sandbox, realized by each runner:
    /workspace   the agent's world (rw)
    /agent       the agent folder (ro)
    /rules.md    the experiment rules (ro)
    /eval        the eval harness (ro, eval sandboxes only)
    /result      where eval writes metrics (rw, eval sandboxes only)

Container runners mount these literally. The local dev runner rewrites the
tokens in command strings and env values instead. Experiment commands and
agent commands are written against canonical paths, which is what keeps an
experiment portable across runners. Programs running inside a sandbox should
locate these paths via the AR_* env vars, never by hardcoding.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..experiment import Environment


@dataclass
class Mount:
    source: Path
    target: str  # canonical path, e.g. "/workspace"
    mode: str = "rw"  # "rw" | "ro"


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class Process(ABC):
    """A long-running command inside a sandbox (the agent)."""

    @abstractmethod
    def poll(self) -> int | None:
        """Exit code if finished, else None."""

    @abstractmethod
    def terminate(self, grace_seconds: float = 10.0) -> None: ...


class Sandbox(ABC):
    @abstractmethod
    def exec(self, command: str, timeout: float | None = None) -> ExecResult: ...

    @abstractmethod
    def spawn(self, command: str, log_path: Path) -> Process: ...

    @abstractmethod
    def destroy(self) -> None: ...


@dataclass
class SandboxSpec:
    mounts: list[Mount] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    resources: dict = field(default_factory=dict)
    workdir: str = "/workspace"
    name: str = "sandbox"


class Runner(ABC):
    name: str = "base"

    @abstractmethod
    def build(self, environment: Environment, experiment_dir: Path, cache_dir: Path) -> str:
        """Prepare the cached environment image. Returns an image reference
        that goes into every submit record as provenance."""

    @abstractmethod
    def provision(self, image: str, spec: SandboxSpec) -> Sandbox: ...

    def build_layer(self, image: str, commands: list[str], cache_key: str) -> str:
        """Optional extra image layer (an agent's CLI installs). Runners
        without images return the image unchanged; the agent's tools are
        then expected on the host."""
        return image

    def interactive_shell(self, image: str, mounts: list[Mount]) -> int:
        """Drop the user into a shell in a sandbox (for `ar auth`). Only
        container runners support this."""
        raise NotImplementedError(f"runner '{self.name}' has no interactive shell")

    def api_host(self) -> str:
        """Hostname at which a sandboxed process reaches the kernel API."""
        return "127.0.0.1"

    def api_bind(self) -> str:
        """Interface the kernel API server binds to for this runner."""
        return "127.0.0.1"

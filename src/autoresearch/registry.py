"""Resolution of agents and runners by name.

Agents are content: a folder with agent.yaml, resolved from --agent as either
a path or a name looked up under ./agents. Runners are infrastructure: code
registered here by name. Adding either touches nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .runners.base import Runner


@dataclass
class AgentSpec:
    path: Path
    name: str
    description: str
    command: str
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[dict] = field(default_factory=list)  # extra mounts (credentials etc.)
    image_setup: list[str] = field(default_factory=list)  # container layer commands
    auth_seed: dict | None = None  # {host: ~/.claude, files: [...]} to copy from
    auth_help: str = ""  # printed inside the `ar auth` shell session


class RegistryError(Exception):
    pass


PACKAGED_AGENTS = Path(__file__).parent / "agents"


def resolve_agent(name_or_path: str, search_root: Path) -> AgentSpec:
    """Resolution order: explicit path, ./agents/<name> in the working
    directory (a local agent shadows a bundled one of the same name),
    then the agents bundled inside the installed package."""
    candidates = [
        Path(name_or_path),
        search_root / "agents" / name_or_path,
        PACKAGED_AGENTS / name_or_path,
    ]
    agent_dir = next((c for c in candidates if (c / "agent.yaml").is_file()), None)
    if agent_dir is None:
        raise RegistryError(
            f"agent '{name_or_path}' not found (looked for agent.yaml in: "
            + ", ".join(str(c) for c in candidates) + ")"
        )
    raw = yaml.safe_load((agent_dir / "agent.yaml").read_text()) or {}
    if not raw.get("command"):
        raise RegistryError(f"{agent_dir}/agent.yaml must define 'command'")
    return AgentSpec(
        path=agent_dir.resolve(),
        name=raw.get("name", agent_dir.name),
        description=raw.get("description", ""),
        command=raw["command"],
        env={k: str(v) for k, v in (raw.get("env") or {}).items()},
        mounts=list(raw.get("mounts") or []),
        image_setup=list(raw.get("image_setup") or []),
        auth_seed=raw.get("auth_seed"),
        auth_help=raw.get("auth_help", ""),
    )


RUNNERS: dict[str, str] = {
    "local": "subprocess on the host, no isolation, instant (development)",
    "apple": "Apple containers, lightweight VMs, the macOS default",
    "docker": "Docker containers, host.docker.internal API path",
    "apptainer": "Apptainer/Singularity for Slurm clusters, fakeroot builds",
}


def resolve_runner(name: str) -> Runner:
    if name == "local":
        from .runners.local import LocalRunner
        return LocalRunner()
    if name == "apple":
        from .runners.apple import AppleRunner
        return AppleRunner()
    if name == "docker":
        from .runners.docker import DockerRunner
        return DockerRunner()
    if name == "apptainer":
        from .runners.apptainer import ApptainerRunner
        return ApptainerRunner()
    raise RegistryError(f"unknown runner '{name}' (available: {', '.join(RUNNERS)})")

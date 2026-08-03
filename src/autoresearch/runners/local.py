"""Local subprocess runner: no isolation, instant startup.

For development and for experiments you trust. The canonical paths are
realized by string rewriting: /workspace, /eval, /result, /agent, /rules.md
in command strings and env values are replaced with the real host paths.
Mount modes are advisory here; there is no enforcement without a container.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
from pathlib import Path

from ..experiment import Environment
from .base import ExecResult, Process, Runner, Sandbox, SandboxSpec


class LocalProcess(Process):
    def __init__(self, popen: subprocess.Popen, log_file):
        self._popen = popen
        self._log_file = log_file

    def poll(self) -> int | None:
        code = self._popen.poll()
        if code is not None and not self._log_file.closed:
            self._log_file.close()
        return code

    def terminate(self, grace_seconds: float = 10.0) -> None:
        if self._popen.poll() is not None:
            return
        # The agent may have children (training jobs); signal the group.
        try:
            os.killpg(os.getpgid(self._popen.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            self._popen.terminate()
        try:
            self._popen.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self._popen.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self._popen.kill()
        if not self._log_file.closed:
            self._log_file.close()


class LocalSandbox(Sandbox):
    def __init__(self, spec: SandboxSpec):
        self.spec = spec
        self._path_map = {m.target: str(m.source) for m in spec.mounts}
        if self._path_map:
            # Single pass, longest token first, and only as a standalone path
            # root: /eval must not match inside /evals/, /workspace must not
            # match as the tail of /evals/00001/workspace, and substituted
            # host paths must never be rescanned for tokens.
            alternation = "|".join(
                re.escape(t) for t in sorted(self._path_map, key=len, reverse=True)
            )
            self._token_re = re.compile(f"(?<![\\w./-])({alternation})(?=$|[/\\s'\":,])")
        else:
            self._token_re = None
        workdir = self._rewrite(spec.workdir)
        self._cwd = Path(workdir) if Path(workdir).is_dir() else Path.cwd()

    def _rewrite(self, text: str) -> str:
        if self._token_re is None:
            return text
        return self._token_re.sub(lambda m: self._path_map[m.group(1)], text)

    def _env(self) -> dict:
        env = dict(os.environ)
        for key, value in self.spec.env.items():
            env[key] = self._rewrite(value)
        return env

    def exec(self, command: str, timeout: float | None = None) -> ExecResult:
        try:
            proc = subprocess.run(
                self._rewrite(command),
                shell=True,
                cwd=self._cwd,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return ExecResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            return ExecResult(
                returncode=-1,
                stdout=(e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                stderr=(e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                timed_out=True,
            )

    def spawn(self, command: str, log_path: Path) -> Process:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")
        popen = subprocess.Popen(
            self._rewrite(command),
            shell=True,
            cwd=self._cwd,
            env=self._env(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group, so terminate() can reap children
        )
        return LocalProcess(popen, log_file)

    def destroy(self) -> None:
        pass  # nothing provisioned, nothing to tear down


class LocalRunner(Runner):
    name = "local"

    def build(self, environment: Environment, experiment_dir: Path, cache_dir: Path) -> str:
        """Run setup commands once per environment spec, directly on the host.

        The "image" is a content hash of the environment section, recorded as
        provenance. A marker file keyed by that hash makes setup run once.
        """
        spec_hash = hashlib.sha256(
            json.dumps({"base": environment.base, "setup": environment.setup}, sort_keys=True).encode()
        ).hexdigest()[:12]
        image = f"local:{spec_hash}"
        marker = cache_dir / f"setup-{spec_hash}.done"
        if environment.setup and not marker.exists():
            sandbox = LocalSandbox(SandboxSpec(mounts=[], env={}, workdir=str(experiment_dir)))
            for command in environment.setup:
                result = sandbox.exec(command, timeout=1800)
                if not result.ok:
                    raise RuntimeError(
                        f"environment setup failed: {command}\n{result.stdout}\n{result.stderr}"
                    )
        cache_dir.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return image

    def provision(self, image: str, spec: SandboxSpec) -> Sandbox:
        return LocalSandbox(spec)

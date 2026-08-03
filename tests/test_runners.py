"""Runner unit tests that need no daemon: argument construction and
registry resolution. Live end-to-end coverage happens in test_kernel.py
via the local runner (and manually on apple/docker/apptainer hosts)."""

from pathlib import Path

import pytest

from autoresearch.registry import RUNNERS, RegistryError, resolve_runner
from autoresearch.runners.apptainer import ApptainerRunner, build_exec_args
from autoresearch.runners.base import Mount, SandboxSpec


def test_registry_names_resolve():
    for name in ("local", "apple", "docker", "apptainer"):
        assert resolve_runner(name).name == name
        assert name in RUNNERS
    with pytest.raises(RegistryError, match="unknown runner"):
        resolve_runner("kubernetes")


def test_apptainer_exec_args(tmp_path):
    spec = SandboxSpec(
        mounts=[
            Mount(tmp_path / "ws", "/workspace", "rw"),
            Mount(tmp_path / "eval", "/eval", "ro"),
        ],
        env={"AR_API_URL": "http://127.0.0.1:9"},
        resources={"cpu": 4, "gpu": 1},
        workdir="/workspace",
    )
    args = build_exec_args("apptainer", "/cache/env", spec, [])
    assert args[:2] == ["apptainer", "exec"]
    assert "--containall" in args and "--cleanenv" in args
    assert f"{tmp_path / 'ws'}:/workspace" in args
    assert f"{tmp_path / 'eval'}:/eval:ro" in args
    assert "AR_API_URL=http://127.0.0.1:9" in args
    assert "--nv" in args, "gpu>0 must pass --nv"
    assert args[-1] == "/cache/env"


def test_apptainer_no_gpu_no_nv(tmp_path):
    spec = SandboxSpec(mounts=[], env={}, resources={"gpu": 0}, workdir="/workspace")
    assert "--nv" not in build_exec_args("apptainer", "img", spec, [])


def test_apptainer_rejects_dockerfile(tmp_path):
    runner = ApptainerRunner()
    with pytest.raises(RuntimeError, match="cannot build Dockerfiles"):
        runner._resolve_base("docker/Dockerfile", tmp_path)


def test_apptainer_base_resolution(tmp_path):
    runner = ApptainerRunner()
    assert runner._resolve_base("python:3.12-slim", tmp_path) == "docker://python:3.12-slim"
    assert runner._resolve_base("docker://x/y:z", tmp_path) == "docker://x/y:z"
    (tmp_path / "env.def").write_text("Bootstrap: docker\nFrom: python:3.12-slim\n")
    assert runner._resolve_base("env.def", tmp_path) == str(tmp_path / "env.def")

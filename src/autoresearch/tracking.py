"""Workspace tracking: the git layer of the kernel.

Contract (DESIGN.md):
- The kernel owns a git repository whose git-dir lives OUTSIDE the sandbox and
  whose work tree is the mounted workspace. There is no .git inside the
  sandbox: the agent cannot tamper with history, and its own git usage cannot
  collide with the kernel's.
- Track by default, exclude by predicate:
    * paths starting with "."            -> ignored
    * default junk list + experiment extras -> ignored
    * files >= max_file_mb               -> hash + size recorded in the
      manifest, bytes not stored
- One kernel commit per submit, --allow-empty so every submit maps to a
  commit. The large-file manifest rides in the commit message body and in the
  submit record.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
from pathlib import Path

from .record import LargeFile

KERNEL_AUTHOR = "autoresearch-kernel"
KERNEL_EMAIL = "kernel@autoresearch.local"


class Tracker:
    """One tracker per (branch, work tree). Multiple trackers may share one
    git_dir: commits are built with plumbing (a private index file,
    write-tree, commit-tree, update-ref), never touching HEAD or the shared
    index, so parallel agents on separate branches cannot race each other
    and still share one object store (which is what makes cross-agent
    diffs work)."""

    def __init__(self, git_dir: str | Path, work_tree: str | Path,
                 max_file_mb: float, ignores: list[str], branch: str = "main"):
        self.git_dir = Path(git_dir).resolve()
        self.work_tree = Path(work_tree).resolve()
        self.max_file_bytes = int(max_file_mb * 1024 * 1024)
        self.ignores = ignores
        self.branch = branch
        self._hash_cache_path = self.git_dir / f"ar_hash_cache_{branch}.json"
        self._index_path = self.git_dir / f"ar_index_{branch}"
        self._excludes_path = self.git_dir / f"ar_excludes_{branch}"

    # ── git plumbing ─────────────────────────────────────────────────

    def _env(self, with_index: bool = False) -> dict:
        env = dict(os.environ)
        env["GIT_DIR"] = str(self.git_dir)
        env["GIT_WORK_TREE"] = str(self.work_tree)
        env["GIT_AUTHOR_NAME"] = KERNEL_AUTHOR
        env["GIT_AUTHOR_EMAIL"] = KERNEL_EMAIL
        env["GIT_COMMITTER_NAME"] = KERNEL_AUTHOR
        env["GIT_COMMITTER_EMAIL"] = KERNEL_EMAIL
        if with_index:
            env["GIT_INDEX_FILE"] = str(self._index_path)
        return env

    def _git(self, *args: str, check: bool = True, with_index: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            env=self._env(with_index),
            cwd=self.work_tree,
            capture_output=True,
            text=True,
            check=check,
        )

    def init(self) -> None:
        self.work_tree.mkdir(parents=True, exist_ok=True)
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.mkdir(parents=True, exist_ok=True)
            self._git("init", "--initial-branch=main")

    # ── exclusion rules ──────────────────────────────────────────────

    def _dir_excluded(self, name: str) -> bool:
        if name.startswith("."):
            return True
        for pattern in self.ignores:
            if pattern.endswith("/") and fnmatch.fnmatch(name, pattern[:-1]):
                return True
        return False

    def _file_excluded(self, name: str) -> bool:
        if name.startswith("."):
            return True
        for pattern in self.ignores:
            if not pattern.endswith("/") and fnmatch.fnmatch(name, pattern):
                return True
        return False

    def scan_large_files(self) -> list[LargeFile]:
        """Walk the workspace; hash every tracked-path file >= the threshold.

        Hashes are cached by (size, mtime_ns) so unchanged checkpoints are
        not re-read on every submit.
        """
        cache = self._load_hash_cache()
        fresh_cache: dict[str, dict] = {}
        manifest: list[LargeFile] = []

        for root, dirs, files in os.walk(self.work_tree):
            dirs[:] = [d for d in dirs if not self._dir_excluded(d)]
            for fname in files:
                if self._file_excluded(fname):
                    continue
                fpath = Path(root) / fname
                try:
                    stat = fpath.stat()
                except OSError:
                    continue  # deleted mid-walk by the agent; its sandbox, its rules
                if stat.st_size < self.max_file_bytes:
                    continue
                rel = str(fpath.relative_to(self.work_tree))
                cached = cache.get(rel)
                if cached and cached["size"] == stat.st_size and cached["mtime_ns"] == stat.st_mtime_ns:
                    digest = cached["sha256"]
                else:
                    digest = _sha256_file(fpath)
                fresh_cache[rel] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}
                manifest.append(LargeFile(
                    path=rel,
                    size_mb=round(stat.st_size / (1024 * 1024), 2),
                    sha256=digest,
                ))

        self._save_hash_cache(fresh_cache)
        manifest.sort(key=lambda lf: lf.path)
        return manifest

    def _write_exclude(self, manifest: list[LargeFile]) -> None:
        # Universal rules (dot + junk) go in the shared info/exclude; the
        # per-agent large-file paths go in a per-branch excludes file, so
        # one agent's checkpoint path never hides another agent's small
        # file at the same path.
        info = self.git_dir / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "exclude").write_text("\n".join([
            "# generated by autoresearch; do not edit",
            ".*",  # the dot rule, at every level
            *self.ignores,
        ]) + "\n")
        self._excludes_path.write_text(
            "\n".join(f"/{lf.path}" for lf in manifest) + "\n")

    # ── snapshots ────────────────────────────────────────────────────

    def snapshot(self, label: str) -> tuple[str, list[LargeFile]]:
        """Commit the full workspace tree onto this tracker's branch.

        Plumbing only: private index -> write-tree -> commit-tree ->
        update-ref. HEAD and the shared index are never touched."""
        manifest = self.scan_large_files()
        self._write_exclude(manifest)
        self._git("-c", f"core.excludesFile={self._excludes_path}",
                  "add", "-A", with_index=True)
        tree = self._git("write-tree", with_index=True).stdout.strip()
        message = label
        if manifest:
            message += "\n\nlarge_files: " + json.dumps([lf.__dict__ for lf in manifest])
        parent = self._git("rev-parse", "--verify", "--quiet",
                           f"refs/heads/{self.branch}", check=False).stdout.strip()
        commit_args = ["commit-tree", tree, "-m", message]
        if parent:
            commit_args += ["-p", parent]
        commit = self._git(*commit_args).stdout.strip()
        self._git("update-ref", f"refs/heads/{self.branch}", commit)
        return commit, manifest

    def diff(self, commit_a: str, commit_b: str) -> str:
        return self._git("diff", f"{commit_a}..{commit_b}").stdout

    def ls_tree(self, commit: str) -> list[dict]:
        """Files in a snapshot: [{path, size}]."""
        result = self._git("ls-tree", "-r", "-l", commit, check=False)
        files = []
        for line in result.stdout.splitlines():
            # <mode> blob <hash> <size>\t<path>
            try:
                meta, path = line.split("\t", 1)
                size = int(meta.split()[3])
            except (ValueError, IndexError):
                continue
            files.append({"path": path, "size": size})
        return files

    def show_file(self, commit: str, path: str) -> str | None:
        if ".." in path or path.startswith("/"):
            return None
        result = self._git("show", f"{commit}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    def show_stat(self, commit: str) -> str:
        return self._git("show", "--stat", "--format=%h %s", commit).stdout

    def log(self) -> str:
        return self._git("log", "--oneline", self.branch, check=False).stdout

    # ── hash cache ───────────────────────────────────────────────────

    def _load_hash_cache(self) -> dict:
        try:
            return json.loads(self._hash_cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_hash_cache(self, cache: dict) -> None:
        self._hash_cache_path.write_text(json.dumps(cache))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

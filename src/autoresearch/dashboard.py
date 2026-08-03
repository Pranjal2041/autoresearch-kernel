"""The dashboard: a pure observer of events.jsonl and the git history.

Reads the run directory fresh on every request, so it works identically on
live and finished runs and can never influence either. This is the
kernel-side view: test metrics are visible here, by design.

`ar watch <run_dir>` serves one run. `ar watch <runs parent>` serves all
runs under it with a run picker, defaulting to the most recently active.

Endpoints (all JSON unless noted):
    /                     the app (static HTML)
    /api/runs             all runs under the target, newest first
    /api/summary?run=     meta + full records + rules for one run
    /api/submit/{id}      one record + eval logs + timing
    /api/tree/{id}        files in that submit's snapshot
    /api/file/{id}?path=  file content at that snapshot
    /api/diff/{a}/{b}     unified diff between two submits
    /api/agentlog?tail=   tail of the agent log
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .events import EventLog
from .tracking import Tracker

STATIC = Path(__file__).parent / "dashboard_static" / "index.html"
LIVE_WINDOW_SECONDS = 30
MAX_FILE_BYTES = 512 * 1024


def _list_run_dirs(target: Path) -> list[Path]:
    if (target / "events.jsonl").is_file():
        return [target]
    candidates = sorted(
        target.glob("*/events.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [c.parent for c in candidates]


def _replay(run_dir: Path) -> dict:
    """One pass over the event log: records, lifecycle, per-submit timing."""
    records: dict[int, dict] = {}
    timing: dict[int, dict] = {}
    started = finished = None
    for event in EventLog(run_dir / "events.jsonl").replay():
        etype = event.get("type")
        if etype == "submit.updated":
            records[event["record"]["submit_id"]] = event["record"]
        elif etype == "run.started":
            started, finished = event, None
        elif etype == "run.finished":
            finished = event
        elif etype == "eval.started":
            timing.setdefault(event["submit_id"], {})["eval_started"] = event["time"]
        elif etype == "eval.finished":
            timing.setdefault(event["submit_id"], {})["eval_finished"] = event["time"]
    return {"records": records, "timing": timing, "started": started, "finished": finished}


def _run_meta(run_dir: Path) -> dict:
    try:
        return json.loads((run_dir / "run.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _is_live(run_dir: Path, finished) -> bool:
    if finished is not None:
        return False
    try:
        age = time.time() - (run_dir / "events.jsonl").stat().st_mtime
    except OSError:
        return False
    return age < LIVE_WINDOW_SECONDS


def _best(records: dict[int, dict], direction: str) -> dict | None:
    best = None
    for rec in records.values():
        if rec.get("status") != "scored" or not rec.get("metric"):
            continue
        if best is None:
            best = rec
        else:
            a, b = rec["metric"]["value"], best["metric"]["value"]
            if (a > b) if direction == "maximize" else (a < b):
                best = rec
    return best


def summarize_run(run_dir: Path) -> dict:
    state = _replay(run_dir)
    meta = _run_meta(run_dir)
    rules = ""
    rules_path = run_dir / "rules.md"
    if rules_path.is_file():
        rules = rules_path.read_text()
    return {
        "run_name": run_dir.name,
        "run_dir": str(run_dir),
        "meta": meta,
        "started": state["started"],
        "finished": state["finished"],
        "live": _is_live(run_dir, state["finished"]),
        "records": [state["records"][i] for i in sorted(state["records"])],
        "timing": state["timing"],
        "rules": rules,
    }


def run_card(run_dir: Path) -> dict:
    state = _replay(run_dir)
    meta = _run_meta(run_dir)
    direction = (meta.get("objective") or {}).get("direction", "minimize")
    best = _best(state["records"], direction)
    return {
        "name": run_dir.name,
        "experiment": meta.get("experiment_name", ""),
        "agent": meta.get("agent", ""),
        "runner": meta.get("runner", ""),
        "created": meta.get("created", ""),
        "live": _is_live(run_dir, state["finished"]),
        "finished_reason": (state["finished"] or {}).get("reason"),
        "submits": len(state["records"]),
        "metric": (meta.get("objective") or {}).get("metric", "metric"),
        "direction": direction,
        "best": best["metric"]["value"] if best else None,
    }


class Api:
    def __init__(self, target: Path):
        self.target = target

    def resolve(self, query: dict) -> Path | None:
        runs = _list_run_dirs(self.target)
        if not runs:
            return None
        wanted = (query.get("run") or [None])[0]
        if wanted:
            for run_dir in runs:
                if run_dir.name == wanted:
                    return run_dir
            return None
        return runs[0]

    def _tracker(self, run_dir: Path) -> Tracker:
        return Tracker(run_dir / "repo.git", run_dir / "workspace", 5.0, [])

    def _records(self, run_dir: Path) -> dict[int, dict]:
        return _replay(run_dir)["records"]

    # ── route handlers, each returns (status, payload) ───────────────

    def runs(self, query) -> tuple[int, object]:
        return 200, [run_card(d) for d in _list_run_dirs(self.target)]

    def summary(self, query) -> tuple[int, object]:
        run_dir = self.resolve(query)
        if run_dir is None:
            return 404, {"error": "run not found"}
        return 200, summarize_run(run_dir)

    def submit(self, query, submit_id: int) -> tuple[int, object]:
        run_dir = self.resolve(query)
        if run_dir is None:
            return 404, {"error": "run not found"}
        state = _replay(run_dir)
        record = state["records"].get(submit_id)
        if record is None:
            return 404, {"error": f"no submit {submit_id}"}
        eval_dir = run_dir / "evals" / f"{submit_id:05d}"
        logs = {}
        for split in ("val", "test"):
            log_path = eval_dir / f"eval_{split}.log"
            if log_path.is_file():
                logs[split] = log_path.read_text(errors="replace")[-MAX_FILE_BYTES:]
        return 200, {"record": record, "eval_logs": logs,
                     "timing": state["timing"].get(submit_id, {})}

    def tree(self, query, submit_id: int) -> tuple[int, object]:
        run_dir = self.resolve(query)
        if run_dir is None:
            return 404, {"error": "run not found"}
        record = self._records(run_dir).get(submit_id)
        if record is None:
            return 404, {"error": f"no submit {submit_id}"}
        return 200, {"files": self._tracker(run_dir).ls_tree(record["commit"]),
                     "large_files": record.get("large_files") or []}

    def file(self, query, submit_id: int) -> tuple[int, object]:
        run_dir = self.resolve(query)
        if run_dir is None:
            return 404, {"error": "run not found"}
        record = self._records(run_dir).get(submit_id)
        path = (query.get("path") or [None])[0]
        if record is None or not path:
            return 404, {"error": "unknown submit or missing path"}
        content = self._tracker(run_dir).show_file(record["commit"], path)
        if content is None:
            return 404, {"error": f"no file {path} in submit {submit_id}"}
        return 200, {"path": path, "content": content[:MAX_FILE_BYTES],
                     "truncated": len(content) > MAX_FILE_BYTES}

    def diff(self, query, a: int, b: int) -> tuple[int, object]:
        run_dir = self.resolve(query)
        if run_dir is None:
            return 404, {"error": "run not found"}
        records = self._records(run_dir)
        if a not in records or b not in records:
            return 404, {"error": "unknown submit id"}
        diff = self._tracker(run_dir).diff(records[a]["commit"], records[b]["commit"])
        return 200, {"a": a, "b": b, "diff": diff}

    VIZ_TYPES = {"viz.svg": "image/svg+xml", "viz.png": "image/png", "viz.html": "text/html"}

    def viz_file(self, query, submit_id: int) -> tuple[Path, str] | None:
        run_dir = self.resolve(query)
        if run_dir is None:
            return None
        viz_dir = run_dir / "evals" / f"{submit_id:05d}" / "viz"
        for name, content_type in self.VIZ_TYPES.items():
            if (viz_dir / name).is_file():
                return viz_dir / name, content_type
        return None

    def vizinfo(self, query, submit_id: int) -> tuple[int, object]:
        found = self.viz_file(query, submit_id)
        if found:
            return 200, {"exists": True, "kind": found[1]}
        run_dir = self.resolve(query)
        error_path = (run_dir / "evals" / f"{submit_id:05d}" / "viz" / "viz_error.txt") if run_dir else None
        if error_path and error_path.is_file():
            return 200, {"exists": False, "error": error_path.read_text()[:2000]}
        return 200, {"exists": False}

    def agentlog(self, query) -> tuple[int, object]:
        run_dir = self.resolve(query)
        if run_dir is None:
            return 404, {"error": "run not found"}
        tail = int((query.get("tail") or ["400"])[0])
        log_path = run_dir / "agent.log"
        if not log_path.is_file():
            return 200, {"content": ""}
        lines = log_path.read_text(errors="replace").splitlines()
        return 200, {"content": "\n".join(lines[-tail:])}


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            parts = [p for p in url.path.split("/") if p]
            try:
                if not parts:
                    self._send(200, "text/html; charset=utf-8", STATIC.read_bytes())
                    return
                if parts[0] != "api":
                    self._send(404, "application/json", b'{"error": "not found"}')
                    return
                if len(parts) == 3 and parts[1] == "viz":  # raw artifact bytes
                    found = api.viz_file(query, int(parts[2]))
                    if found:
                        self._send(200, found[1], found[0].read_bytes())
                    else:
                        self._send(404, "application/json", b'{"error": "no viz"}')
                    return
                status, payload = self._route(parts[1:], query)
            except Exception as e:  # a bad request must never kill the server
                status, payload = 500, {"error": repr(e)}
            self._send(status, "application/json", json.dumps(payload).encode())

        def _route(self, parts: list[str], query) -> tuple[int, object]:
            match parts:
                case ["runs"]:
                    return api.runs(query)
                case ["summary"]:
                    return api.summary(query)
                case ["submit", sid]:
                    return api.submit(query, int(sid))
                case ["tree", sid]:
                    return api.tree(query, int(sid))
                case ["file", sid]:
                    return api.file(query, int(sid))
                case ["diff", a, b]:
                    return api.diff(query, int(a), int(b))
                case ["vizinfo", sid]:
                    return api.vizinfo(query, int(sid))
                case ["agentlog"]:
                    return api.agentlog(query)
            return 404, {"error": "no such route"}

    return Handler


def serve_dashboard(target: Path, port: int = 8722) -> int:
    target = target.resolve()
    if not _list_run_dirs(target):
        print(f"error: no events.jsonl under {target}")
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Api(target)))
    print(f"dashboard: http://127.0.0.1:{port}  (watching {target}, ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0

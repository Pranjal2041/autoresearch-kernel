"""Scripted test agent. Stdlib only: any agent is just HTTP + files."""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ["AR_API_URL"].rstrip("/")
WORKSPACE = Path(os.environ["AR_WORKSPACE"])

SCHEDULE = [
    ([1.0, -2.0, 0.0], "rough guess toward the targets"),
    ([1.2, -2.5, 0.4], "closer on all three"),
    ([1.3, -2.7, 0.5], "matching val targets exactly"),
    ([1.25, -2.6, 0.45], "deliberate step backward (best must not move)"),
]


def call(method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main() -> None:
    status, info = call("GET", "/experiment")
    print(f"experiment: {info['name']}  objective: {info['objective']}")

    # Exercise the tracking rules once: none of these may enter git history,
    # and the large file must appear in the manifest as hash-only.
    (WORKSPACE / ".secret").write_text("dotfiles are invisible to history")
    (WORKSPACE / "__pycache__").mkdir(exist_ok=True)
    (WORKSPACE / "__pycache__" / "junk.pyc").write_bytes(b"\x00" * 128)
    (WORKSPACE / "data.bin").write_bytes(b"\x01" * (6 * 1024 * 1024))

    for i, (params, notes) in enumerate(SCHEDULE, start=1):
        (WORKSPACE / "solution.py").write_text(f"PARAMS = {params}\n")
        status, body = call("POST", "/submit", {"notes": notes})
        if status != 200:
            print(f"submit rejected ({status}): {body}")
            return
        submit_id = body["submit_id"]

        if i == 1:
            # Concurrency probe: a second submit while the first is scoring
            # must be rejected with 409, never queued.
            status2, body2 = call("POST", "/submit", {"notes": "concurrent probe"})
            print(f"concurrent submit -> {status2} ({body2.get('error', 'accepted?!')})")

        while True:
            status, record = call("GET", f"/submit/{submit_id}")
            if record["status"] in ("scored", "failed"):
                break
            time.sleep(0.2)
        metric = record.get("metric") or {}
        assert "test_metric" not in record, "kernel leaked the test metric to the agent API"
        print(f"submit {submit_id}: {record['status']} "
              f"{metric.get('name')}={metric.get('value')} best={record['best_so_far']}")

    status, best = call("GET", "/best")
    if status == 200:
        print(f"final best: submit {best['submit_id']} value {best['metric']['value']}")
    else:
        print("final best: none (no scored submits)")


if __name__ == "__main__":
    main()

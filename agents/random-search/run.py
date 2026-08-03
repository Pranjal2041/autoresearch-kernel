"""Adaptive gaussian random search over the float literals of solution.py.

No LLM anywhere. The candidate is the ordered list of float literals in
the workspace's solution.py; each iteration perturbs them, submits, and
keeps the perturbation only if the official score improved. The step size
follows a 1/5th-success rule: grow on success, shrink on failure.

Stdlib only, deterministic under AR_RS_SEED.
"""

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ["AR_API_URL"].rstrip("/")
SOLUTION = Path(os.environ["AR_WORKSPACE"]) / "solution.py"
FLOAT_RE = re.compile(r"(?<![\w.])-?\d+\.\d+(?:e-?\d+)?(?![\w.])")


def call(method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def read_params() -> list[float]:
    return [float(m) for m in FLOAT_RE.findall(SOLUTION.read_text())]


def write_params(params: list[float]) -> None:
    values = iter(params)
    SOLUTION.write_text(FLOAT_RE.sub(lambda m: repr(next(values)), SOLUTION.read_text()))


def score_of(record: dict) -> float | None:
    return record["metric"]["value"] if record.get("metric") else None


def submit_and_wait(notes: str) -> dict | None:
    rng = random.Random()
    for attempt in range(120):
        status, body = call("POST", "/submit", {"notes": notes})
        if status == 200:
            break
        if status == 409 and "stopping" not in json.dumps(body):
            # jittered retry: with parallel agents on one submit lane, a
            # fixed backoff loses every race to a faster sibling
            time.sleep(rng.uniform(0.2, 0.9))
            continue
        return None
    else:
        return None
    while True:
        status, record = call("GET", f"/submit/{body['submit_id']}")
        if record["status"] in ("scored", "failed"):
            return record
        time.sleep(0.3)


def main() -> None:
    rng = random.Random(int(os.environ.get("AR_RS_SEED", "0")))
    _, info = call("GET", "/experiment")
    direction = info["objective"]["direction"]
    better = (lambda a, b: a > b) if direction == "maximize" else (lambda a, b: a < b)
    budget = info["budget"]
    remaining = (budget["max_submits"] or 10**9) - budget["submits_used"]

    params = read_params()
    if not params:
        print("random-search: no float literals in solution.py to optimize")
        return
    print(f"random-search: {len(params)} parameters, {remaining} submits, {direction}")

    record = submit_and_wait("random-search: baseline (unmodified seed)")
    if record is None or record["status"] == "failed":
        print(f"baseline failed: {record and record.get('error')}")
        return
    best_params, best_score = list(params), score_of(record)
    sigma = 1.0
    remaining -= 1

    while remaining > 0:
        candidate = [p + rng.gauss(0.0, sigma) for p in best_params]
        write_params(candidate)
        record = submit_and_wait(f"random-search: sigma={sigma:.3g}")
        remaining -= 1
        if record is None:
            break
        value = score_of(record)
        if record["status"] == "scored" and value is not None and better(value, best_score):
            best_params, best_score = candidate, value
            sigma *= 1.5
            print(f"improved: {best_score} (sigma -> {sigma:.3g})")
        else:
            write_params(best_params)  # revert: keep/revert is agent policy
            sigma = max(sigma * 0.6, 1e-12)
            print(f"no improvement ({value}); sigma -> {sigma:.3g}")

    write_params(best_params)
    print(f"done: best {best_score}")


if __name__ == "__main__":
    main()

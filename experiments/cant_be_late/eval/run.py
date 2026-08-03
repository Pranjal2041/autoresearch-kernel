"""Official eval for cant_be_late.

Runs the workspace's strategy through the sky_spot simulator (vendored
from the optimize_anything artifact, which vendors it from the Can't Be
Late paper's SkyPilot simulator) over real spot-availability traces.

Both splits use us-west-2b_k80_1 traces (the zone with real spot churn;
us-west-2a's sampled traces have continuous availability and carry no
signal). val and test are disjoint trace sets of mixed difficulty,
4 traces x 3 deadline configs each.

Metric: mean cost across the 12 simulations (lower is better). The
simulator enforces the deadline as a hard constraint; a strategy that
would miss a deadline fails its run, which fails the submit.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SIMULATOR = Path(__file__).parent / "simulator"
TRACE_ROOT = SIMULATOR / "traces_root" / "ddl=search+task=48+overhead=0.02" / "real"
OVERHEAD = 0.02
TRACE_IDS = {"val": [0, 8, 33, 61], "test": [9, 20, 42, 99]}
ENV_ZONE = "us-west-2b_k80_1"
JOB_CONFIGS = [(48, 52), (48, 70), (48, 92)]  # (duration, deadline) hours
SIM_TIMEOUT = 120


def run_sim(strategy: Path, trace: Path, duration: int, deadline: int, out_dir: Path) -> float:
    cmd = [
        sys.executable, str(SIMULATOR / "main.py"),
        f"--strategy-file={strategy}",
        "--env=trace",
        f"--trace-file={trace}",
        f"--task-duration-hours={duration}",
        f"--deadline-hours={deadline}",
        f"--restart-overhead-hours={OVERHEAD}",
        "--silent",
        f"--output-dir={out_dir}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=SIM_TIMEOUT, cwd=SIMULATOR)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-6:]
        raise SystemExit(
            f"simulation failed on {trace.name} (dur={duration}, ddl={deadline}): "
            + " | ".join(tail))
    for line in result.stdout.splitlines():
        if "mean:" in line:
            return float(line.split("mean:")[1].split(";")[0].strip())
    raise SystemExit(f"no cost in simulator output for {trace.name} "
                     f"(dur={duration}, ddl={deadline})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("val", "test"), required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    strategy = Path(os.environ["AR_WORKSPACE"]) / "solution.py"
    result_dir = Path(os.environ["AR_RESULT"])
    trace_dir = TRACE_ROOT / ENV_ZONE / "traces" / "random_start"

    costs, detail = [], {}
    for trace_id in TRACE_IDS[args.split]:
        trace = trace_dir / f"{trace_id}.json"
        for duration, deadline in JOB_CONFIGS:
            cost = run_sim(strategy, trace, duration, deadline, result_dir / "sim_out")
            costs.append(cost)
            key = f"trace{trace_id}_d{duration}_ddl{deadline}"
            detail[key] = cost
            print(f"{key:<28} cost={cost:.4f}")

    mean_cost = sum(costs) / len(costs)
    Path(args.out).write_text(json.dumps({"mean_cost": mean_cost, "per_run": detail}))
    print(f"mean cost ({args.split}, {len(costs)} sims) = {mean_cost:.6f}")


if __name__ == "__main__":
    main()

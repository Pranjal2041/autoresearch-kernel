"""Agent-side harness: score solution.py on the VAL traces exactly the
way the official eval does. Free to run, costs no submits:

    python3 local_eval.py

The test traces come from a different availability zone and are hidden
on the kernel side; a strategy tuned to val-trace quirks shows up as
val-test divergence on the dashboard.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

SIM = Path(__file__).parent / "sim"
TRACE_DIR = (SIM / "traces_root" / "ddl=search+task=48+overhead=0.02"
             / "real" / "us-west-2b_k80_1" / "traces" / "random_start")
OVERHEAD = 0.02
TRACE_IDS = [0, 8, 33, 61]
JOB_CONFIGS = [(48, 52), (48, 70), (48, 92)]


def run_sim(strategy, trace, duration, deadline, out_dir):
    cmd = [
        sys.executable, str(SIM / "main.py"),
        f"--strategy-file={strategy}", "--env=trace", f"--trace-file={trace}",
        f"--task-duration-hours={duration}", f"--deadline-hours={deadline}",
        f"--restart-overhead-hours={OVERHEAD}", "--silent",
        f"--output-dir={out_dir}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=SIM)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-6:]
        raise SystemExit(f"FAILED {trace.name} d={duration} ddl={deadline}: " + " | ".join(tail))
    for line in result.stdout.splitlines():
        if "mean:" in line:
            return float(line.split("mean:")[1].split(";")[0].strip())
    raise SystemExit(f"no cost in output for {trace.name}")


def main():
    strategy = Path(__file__).parent / "solution.py"
    costs = []
    with tempfile.TemporaryDirectory() as out_dir:
        for trace_id in TRACE_IDS:
            trace = TRACE_DIR / f"{trace_id}.json"
            for duration, deadline in JOB_CONFIGS:
                cost = run_sim(strategy, trace, duration, deadline, out_dir)
                costs.append(cost)
                print(f"trace{trace_id}_d{duration}_ddl{deadline:<3}  cost={cost:.4f}")
    print(f"mean val cost = {sum(costs) / len(costs):.6f}")


if __name__ == "__main__":
    main()

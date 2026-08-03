"""Official eval for blackbox_opt.

Runs the workspace's solve() on each problem in the split under a hard
2000-call budget, and scores from the harness's OWN call log: the metric
is the best value the solver actually evaluated, never what it claims.

Metric: mean normalized regret across problems.
    regret_p = (best_found - fmin) / (random_median - fmin), clipped to [0, 2]
where random_median is the median of 200 seeded uniform samples, computed
fresh and deterministically here. 0.0 means the known optimum on every
problem; 1.0 means no better than median random sampling.
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import problems  # noqa: E402

BUDGET = 2000
RANDOM_BASELINE_SAMPLES = 200


class BudgetExceeded(Exception):
    pass


def random_median(problem, seed: int) -> float:
    rng = np.random.default_rng(seed)
    bounds = np.array(problem.bounds, dtype=float)
    xs = rng.uniform(bounds[:, 0], bounds[:, 1],
                     size=(RANDOM_BASELINE_SAMPLES, len(bounds)))
    return float(np.median([problem.evaluate(x) for x in xs]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("val", "test"), required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    workspace = Path(os.environ["AR_WORKSPACE"])
    spec = importlib.util.spec_from_file_location("solution", workspace / "solution.py")
    solution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solution)

    regrets, detail = [], {}
    for name, problem, seed in problems.for_split(args.split):
        np.random.seed(seed)  # solvers may use global numpy randomness
        calls: list[float] = []

        def objective(x, _problem=problem, _calls=calls):
            if len(_calls) >= BUDGET:
                raise BudgetExceeded()
            value = float(_problem.evaluate(np.asarray(x, dtype=float)))
            _calls.append(value)
            return value

        config = {
            "bounds": [list(map(float, b)) for b in problem.bounds],
            "dim": int(problem.dim),
            "budget": BUDGET,
        }
        try:
            solution.solve(objective, config)
        except BudgetExceeded:
            pass  # budget spent; score what was actually evaluated
        except Exception as e:
            raise SystemExit(f"solver crashed on {name}: {type(e).__name__}: {e}")
        if not calls:
            raise SystemExit(f"solver made no objective calls on {name}")

        best = min(calls)
        fmin = problems.fmin_of(problem)
        baseline = random_median(problem, seed)
        regret = (best - fmin) / max(baseline - fmin, 1e-12)
        regret = float(np.clip(regret, 0.0, 2.0))
        regrets.append(regret)
        detail[name] = {"best": best, "fmin": fmin, "random_median": baseline,
                        "regret": regret, "calls": len(calls)}
        print(f"{name:<24} best={best:.6g}  fmin={fmin:.6g}  regret={regret:.4f}  calls={len(calls)}")

    mean_regret = float(np.mean(regrets))
    Path(args.out).write_text(json.dumps({"regret": mean_regret, "per_problem": detail}))
    print(f"mean normalized regret ({args.split}) = {mean_regret:.6f}")


if __name__ == "__main__":
    main()

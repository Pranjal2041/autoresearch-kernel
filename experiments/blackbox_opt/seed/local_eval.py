"""Agent-side harness: score your solve() on the VAL problems exactly the
way the official eval does. Run freely, it costs no submits:

    python3 local_eval.py

The test problems are hidden; only the kernel has them. A solver that
hard-codes val-problem quirks will show up as val-test divergence on the
run dashboard, and that is exactly what this experiment measures.
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import val_problems  # noqa: E402

BUDGET = 2000
RANDOM_BASELINE_SAMPLES = 200


class BudgetExceeded(Exception):
    pass


def random_median(problem, seed):
    rng = np.random.default_rng(seed)
    bounds = np.array(problem.bounds, dtype=float)
    xs = rng.uniform(bounds[:, 0], bounds[:, 1],
                     size=(RANDOM_BASELINE_SAMPLES, len(bounds)))
    return float(np.median([problem.evaluate(x) for x in xs]))


def main():
    spec = importlib.util.spec_from_file_location(
        "solution", Path(__file__).parent / "solution.py")
    solution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solution)

    regrets = []
    for name, problem, seed in val_problems.for_split():
        np.random.seed(seed)
        calls = []

        def objective(x, _problem=problem, _calls=calls):
            if len(_calls) >= BUDGET:
                raise BudgetExceeded()
            value = float(_problem.evaluate(np.asarray(x, dtype=float)))
            _calls.append(value)
            return value

        config = {"bounds": [list(map(float, b)) for b in problem.bounds],
                  "dim": int(problem.dim), "budget": BUDGET}
        try:
            solution.solve(objective, config)
        except BudgetExceeded:
            pass
        best = min(calls)
        fmin = val_problems.fmin_of(problem)
        baseline = random_median(problem, seed)
        regret = float(np.clip((best - fmin) / max(baseline - fmin, 1e-12), 0.0, 2.0))
        regrets.append(regret)
        print(f"{name:<24} best={best:.6g}  fmin={fmin:.6g}  regret={regret:.4f}  calls={len(calls)}")
    print(f"mean val regret = {np.mean(regrets):.6f}")


if __name__ == "__main__":
    main()

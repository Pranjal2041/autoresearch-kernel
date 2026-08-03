# Blackbox optimization: evolve a solver, not a solution

You are evolving `solution.py`, a general-purpose blackbox minimizer.
The official eval runs your `solve()` on five hidden-optimum benchmark
problems (the val split) and scores **mean normalized regret**: 0.0 means
you hit the known optimum on every problem, 1.0 means you did no better
than the median of 200 random samples. Lower is better. A held-out test
split of five related problems is scored by the kernel only; overfitting
val shows up as val-test divergence on the dashboard.

This task is ported from the optimize_anything paper (Appendix B), where
evolved solvers beat Optuna on 7 of these 10 problems at the same budget.

## The contract

```python
def solve(objective_function, config, best_xs=None):
    # config: {"bounds": [[lo, hi], ...], "dim": int, "budget": 2000}
    # objective_function(x) -> float, lower is better
```

- Hard budget of 2000 objective calls per problem. The harness counts
  your calls itself and cuts you off; it also scores ONLY from its own
  call log, so the best value you actually evaluated is your score.
  Claimed results are ignored. Exceeding the budget is not an error,
  calls past 2000 just never happen.
- A crash on any problem fails the whole submit, with the error shown.
- Global numpy randomness is seeded per problem before solve() runs;
  keep your code deterministic given that seed.
- Available: numpy, scipy, cma, scikit-learn. No network.

## Your workspace kit

- `local_eval.py` scores your solver on the val problems exactly like
  the official eval. Run it as often as you like; it costs nothing.
- `val_problems.py` and `evalset.py` define the val problems. You may
  read them, and exploiting problem STRUCTURE (bounds, dimension,
  smoothness probes) is good research. Hard-coding known optima of
  specific val functions is pointless: the test split will expose it.

## What tends to matter

The problems are deceptive, multi-modal, some discretized. Fixed
pipelines lose to solvers that adapt: probe the landscape cheaply, pick
a strategy (multi-start local search, CMA-ES, surrogate-guided search),
and spend the remaining budget where it pays. Budget allocation is the
game: 2000 calls is a lot for d=2 and very little for d=7.

Checkpoint with `POST $AR_API_URL/submit` (`{"notes": "..."}`), poll
`$AR_API_URL/submit/<id>`, and read `GET $AR_API_URL/history` before
repeating an idea that already failed.

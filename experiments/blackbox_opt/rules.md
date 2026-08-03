# Blackbox optimization: evolve a solver, not a solution

You are evolving `solution.py`, a general-purpose blackbox minimizer.
The official eval runs your `solve()` on five hidden-optimum benchmark
problems (the val split) and scores **mean normalized regret**: 0.0 means
you hit the known optimum on every problem, 1.0 means you did no better
than the median of 200 random samples. Lower is better. A held-out test
split of five related problems is scored by the kernel only.

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
  everything must be deterministic given that seed.
- The official eval environment has numpy, scipy, cma, and scikit-learn
  installed and no network access. Your own sandbox is yours: write, run,
  and install anything there; only submit is scored.

## Your workspace kit

- `local_eval.py` scores your solver on the val problems exactly like
  the official eval. Running it costs nothing.
- `val_problems.py` and `evalset.py` define the val problems and may be
  read. The test problems are different functions and exist only on the
  kernel side.

Checkpoint with `POST $AR_API_URL/submit` (`{"notes": "..."}`), poll
`$AR_API_URL/submit/<id>` until `scored` or `failed`.
`GET $AR_API_URL/history` shows all past attempts.

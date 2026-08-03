"""The candidate: a blackbox minimizer. The official eval calls solve()
on each problem with a hard budget; the score is the best value among the
objective calls the harness itself observed.

This seed is the optimize_anything paper's own baseline: one random
sample. Everything above it is yours to build.
"""

import numpy as np


def solve(objective_function, config, best_xs=None):
    bounds = np.array(config["bounds"])
    all_attempts = []

    x = np.random.uniform(bounds[:, 0], bounds[:, 1])
    score = objective_function(x)
    all_attempts.append({"x": x.copy(), "score": score})

    return {"x": x, "score": score, "all_attempts": all_attempts}

"""The 10 hardest EvalSet problems from the optimize_anything artifact
(Appendix B, indices [9,10,24,31,38,45,46,51,53,54] of their 56-problem
table), split 5 val / 5 test. Related families deliberately straddle the
split (Easom dim4 in val, dim5 in test) so generalization is measurable.
"""

import evalset

CONFIGS = {
    "val": [
        {"name": "Easom", "dim": 4, "res": None},
        {"name": "McCourt09", "dim": 3, "res": None},
        {"name": "Mishra06", "dim": 2, "res": None},
        {"name": "Pinter", "dim": 2, "res": None},
        {"name": "Sphere", "dim": 7, "res": None},
    ],
    "test": [
        {"name": "Easom", "dim": 5, "res": None},
        {"name": "McCourt16", "dim": 4, "res": 10},
        {"name": "Parsopoulos", "dim": 2, "res": None},
        {"name": "Schwefel36", "dim": 2, "res": None},
        {"name": "StyblinskiTang", "dim": 5, "res": None},
    ],
}


def build(config):
    problem = getattr(evalset, config["name"])(dim=config["dim"])
    if config["res"] is not None:
        problem = evalset.Discretizer(problem, res=config["res"])
    return problem


def fmin_of(problem) -> float:
    if hasattr(problem, "fmin") and problem.fmin is not None:
        return float(problem.fmin)
    return float(problem.func.fmin)  # Discretizer wraps the base function


def label(config) -> str:
    res = f"/res{config['res']}" if config["res"] else ""
    return f"{config['name']}-d{config['dim']}{res}"


def for_split(split: str):
    for i, config in enumerate(CONFIGS[split]):
        yield label(config), build(config), 1000 + i if split == "val" else 2000 + i

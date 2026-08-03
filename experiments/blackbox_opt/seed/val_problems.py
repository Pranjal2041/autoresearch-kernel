"""The VAL problem set, identical to the official eval's val split. The
test problems are hidden on the kernel side."""

import evalset

CONFIGS = [
    {"name": "Easom", "dim": 4, "res": None},
    {"name": "McCourt09", "dim": 3, "res": None},
    {"name": "Mishra06", "dim": 2, "res": None},
    {"name": "Pinter", "dim": 2, "res": None},
    {"name": "Sphere", "dim": 7, "res": None},
]


def build(config):
    problem = getattr(evalset, config["name"])(dim=config["dim"])
    if config["res"] is not None:
        problem = evalset.Discretizer(problem, res=config["res"])
    return problem


def fmin_of(problem):
    if hasattr(problem, "fmin") and problem.fmin is not None:
        return float(problem.fmin)
    return float(problem.func.fmin)


def for_split():
    for i, config in enumerate(CONFIGS):
        res = f"/res{config['res']}" if config["res"] else ""
        yield f"{config['name']}-d{config['dim']}{res}", build(config), 1000 + i

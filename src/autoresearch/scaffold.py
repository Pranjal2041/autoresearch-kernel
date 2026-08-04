"""`ark init`: scaffold a new experiment folder.

Generates a working official-mode experiment: a commented experiment.yaml,
rules, a trivial seed, and an eval stub that already runs. `ark validate`
passes on the result, and `ark run` with the random-search agent produces
real scored submits, so the user starts from a green pipeline and edits,
rather than from a blank page.
"""

from __future__ import annotations

from pathlib import Path

EXPERIMENT_YAML = """\
name: {name}
description: >
  TODO: one sentence on what is being optimized.

objective:
  metric: score
  direction: maximize          # minimize | maximize
  mode: official               # official: /eval scores it. reported: the
                               # agent self-reports (see `ark help experiment`)
  eval_command: python3 /eval/run.py --out /result/metrics.json
  metric_path: metrics.json:score
  timeout_seconds: 300
  # test_command: ...          # optional held-out split, kernel-only results
  # viz_command: ...           # optional, renders /result/viz.svg per submit

submit:
  signature:
    notes: {{type: string, required: true}}

environment:
  base: python:3.12-slim       # or a Dockerfile path inside this folder
  resources: {{cpu: 2, mem_gb: 4, gpu: 0, net: false}}
  setup: []                    # e.g. [pip install numpy]

tracking:
  max_file_mb: 5

stop:
  max_submits: 50
  max_hours: 6
"""

RULES_MD = """\
# {name}

TODO: state the game. What is the candidate, what may the agent change,
what is forbidden, and what counts as a better score.

- The candidate is `solution.py` in your workspace.
- Checkpoint with `POST $AR_API_URL/submit` and a JSON body
  `{{"notes": "<what you changed>"}}`, then poll
  `$AR_API_URL/submit/<id>` until `scored` or `failed`.
- `GET $AR_API_URL/history` shows every past attempt.
"""

SEED_SOLUTION = """\
def answer():
    \"\"\"The candidate. The eval harness imports and scores this.\"\"\"
    return 0.0
"""

EVAL_RUN = """\
\"\"\"Official eval stub. Locates the workspace via AR_WORKSPACE (never
hardcode /workspace: canonical paths are runner-specific at runtime),
scores the candidate, writes the metrics file the kernel reads.\"\"\"

import argparse
import importlib.util
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    workspace = Path(os.environ["AR_WORKSPACE"])
    spec = importlib.util.spec_from_file_location("solution", workspace / "solution.py")
    solution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solution)

    score = float(solution.answer())  # TODO: real scoring
    Path(args.out).write_text(json.dumps({"score": score}))
    print(f"score = {score}")


if __name__ == "__main__":
    main()
"""


def init_experiment(path: str | Path) -> Path:
    root = Path(path)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f"{root} already exists and is not empty")
    name = root.name.replace("_", "-")
    (root / "seed").mkdir(parents=True)
    (root / "eval").mkdir()
    (root / "experiment.yaml").write_text(EXPERIMENT_YAML.format(name=name))
    (root / "rules.md").write_text(RULES_MD.format(name=name))
    (root / "seed" / "solution.py").write_text(SEED_SOLUTION)
    (root / "eval" / "run.py").write_text(EVAL_RUN)
    return root

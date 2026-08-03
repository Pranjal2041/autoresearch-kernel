"""Official eval for toy_quadratic.

Reads PARAMS from the submitted workspace, scores MSE against the split's
targets, writes metrics JSON. Locates the workspace via AR_WORKSPACE (never
hardcode /workspace: canonical paths are runner-specific at runtime).
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path

TARGETS = {
    "val": [1.3, -2.7, 0.5],
    "test": [1.35, -2.64, 0.48],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("val", "test"), required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    workspace = Path(os.environ["AR_WORKSPACE"])
    spec = importlib.util.spec_from_file_location("solution", workspace / "solution.py")
    solution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solution)

    params = list(solution.PARAMS)
    targets = TARGETS[args.split]
    if len(params) != len(targets):
        raise SystemExit(f"PARAMS must have {len(targets)} entries, got {len(params)}")
    loss = sum((p - t) ** 2 for p, t in zip(params, targets)) / len(targets)

    Path(args.out).write_text(json.dumps({"val_loss": loss}))
    print(f"{args.split} loss: {loss:.6f}")


if __name__ == "__main__":
    main()

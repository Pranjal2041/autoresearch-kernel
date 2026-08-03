"""Official verifier for circle-packing-26.

Pure stdlib on purpose: the verifier must never break because of the
solution's dependencies. Exact checks with tolerance 1e-9; invalid packings
fail the eval with a precise reason so the agent can fix them.
"""

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path

N = 26
TOL = 1e-9


def fail(reason: str) -> None:
    raise SystemExit(f"INVALID PACKING: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    workspace = Path(os.environ["AR_WORKSPACE"])
    spec = importlib.util.spec_from_file_location("solution", workspace / "solution.py")
    solution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solution)

    centers, radii = solution.construct_packing()
    centers = [(float(c[0]), float(c[1])) for c in centers]
    radii = [float(r) for r in radii]

    if len(centers) != N or len(radii) != N:
        fail(f"need exactly {N} circles, got {len(centers)} centers and {len(radii)} radii")
    for i, ((x, y), r) in enumerate(zip(centers, radii)):
        if not all(map(math.isfinite, (x, y, r))):
            fail(f"circle {i} has a non-finite value")
        if r <= 0:
            fail(f"circle {i} has non-positive radius {r}")
        if x < r - TOL or x > 1 - r + TOL or y < r - TOL or y > 1 - r + TOL:
            fail(f"circle {i} (x={x:.12f}, y={y:.12f}, r={r:.12f}) leaves the unit square")
    for i in range(N):
        for j in range(i + 1, N):
            dx = centers[i][0] - centers[j][0]
            dy = centers[i][1] - centers[j][1]
            need = radii[i] + radii[j]
            if dx * dx + dy * dy < (need - TOL) * (need - TOL):
                gap = need - math.hypot(dx, dy)
                fail(f"circles {i} and {j} overlap by {gap:.3e}")

    total = sum(radii)
    Path(args.out).write_text(json.dumps({"sum_radii": total}))
    print(f"valid packing, sum of radii = {total:.9f}")


if __name__ == "__main__":
    main()

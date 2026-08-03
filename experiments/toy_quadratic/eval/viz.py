"""Tiny SVG viz for toy_quadratic: PARAMS as bars. Exercises the viz hook."""

import importlib.util
import os
from pathlib import Path


def main() -> None:
    workspace = Path(os.environ["AR_WORKSPACE"])
    out = Path(os.environ["AR_RESULT"]) / "viz.svg"
    spec = importlib.util.spec_from_file_location("solution", workspace / "solution.py")
    solution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solution)
    params = list(solution.PARAMS)

    width, height, mid = 360, 160, 80
    scale = 20.0
    bars = []
    for i, p in enumerate(params):
        x = 40 + i * 100
        h = min(abs(float(p)) * scale, 70)
        y = mid - h if p >= 0 else mid
        bars.append(f'<rect x="{x}" y="{y:.1f}" width="48" height="{h:.1f}" fill="#3987e5"/>')
        bars.append(f'<text x="{x + 24}" y="150" fill="#c3c2b7" font-size="11" text-anchor="middle">{float(p):.2f}</text>')
    out.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" font-family="monospace">'
        f'<rect width="{width}" height="{height}" fill="#0e0e0f"/>'
        f'<line x1="0" x2="{width}" y1="{mid}" y2="{mid}" stroke="#38383a"/>'
        + "".join(bars) + "</svg>"
    )


if __name__ == "__main__":
    main()

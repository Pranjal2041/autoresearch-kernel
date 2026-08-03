"""Render the submitted packing as an SVG. Pure stdlib.

Runs in the eval sandbox after scoring (objective.viz_command); writes
/result/viz.svg which the dashboard shows on the submit's viz tab.
"""

import importlib.util
import os
from pathlib import Path

S = 720  # canvas size for the unit square


def main() -> None:
    workspace = Path(os.environ["AR_WORKSPACE"])
    out = Path(os.environ["AR_RESULT"]) / "viz.svg"
    spec = importlib.util.spec_from_file_location("solution", workspace / "solution.py")
    solution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solution)
    centers, radii = solution.construct_packing()

    total = sum(float(r) for r in radii)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S + 40}" '
        f'font-family="monospace">',
        f'<rect x="0" y="0" width="{S}" height="{S}" fill="#0e0e0f" '
        f'stroke="#38383a" stroke-width="1.5"/>',
    ]
    order = sorted(range(len(radii)), key=lambda i: -float(radii[i]))
    for rank, i in enumerate(order):
        (x, y), r = centers[i], float(radii[i])
        # y axis flipped so the square reads with the origin at bottom-left
        parts.append(
            f'<circle cx="{float(x) * S:.2f}" cy="{(1 - float(y)) * S:.2f}" r="{r * S:.2f}" '
            f'fill="rgba(57,135,229,0.28)" stroke="#3987e5" stroke-width="1.5"/>'
        )
        if r * S > 14:  # label circles big enough to hold text
            parts.append(
                f'<text x="{float(x) * S:.2f}" y="{(1 - float(y)) * S + 3:.2f}" fill="#c3c2b7" '
                f'font-size="10" text-anchor="middle">{r:.4f}</text>'
            )
    parts.append(
        f'<text x="10" y="{S + 26}" fill="#c3c2b7" font-size="15">'
        f'n={len(radii)}  sum of radii = {total:.9f}</text>'
    )
    parts.append("</svg>")
    out.write_text("\n".join(parts))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

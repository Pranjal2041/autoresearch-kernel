# Circle packing: n = 26, maximize the sum of radii

Place exactly 26 circles inside the unit square [0,1] x [0,1] so that no two
circles overlap, maximizing the total sum of the 26 radii. Radii may differ
from each other. This is the benchmark task from the AlphaEvolve paper. Its
reported best is 2.635; later systems reached about 2.636. Getting above 2.6
already requires real geometric insight plus numerical refinement.

## Your candidate

`solution.py` in your workspace must expose:

```python
def construct_packing():
    """Returns (centers, radii): centers is a sequence of 26 (x, y) pairs,
    radii a sequence of 26 positive floats."""
```


The verifier calls this function, checks validity exactly, and scores the
sum of radii. Constraints, checked with tolerance 1e-9:
- exactly 26 circles
- every circle fully inside the unit square: r <= x <= 1-r and r <= y <= 1-r
- no overlap: dist(c_i, c_j) >= r_i + r_j for every pair
- all radii > 0

Aim slightly inside the constraints (for example shrink all radii by 1e-9)
so floating point noise never invalidates a good packing.

## Budget and environment

- The whole eval (your construct_packing call included) must finish within
  600 seconds. Keep your own optimization inside roughly 300 seconds.
- Everything must be deterministic or seeded: a submit you cannot reproduce is useless to you.
- You may write and run and install anything in your workspace to test before
  submitting. The official score comes only from submit.
"""Seed: a deliberately weak baseline. A 5x5 grid of equal circles plus one
tiny circle tucked into a corner gap. Sum of radii = 2.51."""


def construct_packing():
    centers = []
    radii = []
    r = 0.1
    for i in range(5):
        for j in range(5):
            centers.append((r + i * 2 * r, r + j * 2 * r))
            radii.append(r - 1e-9)
    # the diagonal gap between four grid circles has room for r = 0.1*(sqrt(2)-1)
    centers.append((0.2, 0.2))
    radii.append(0.01)
    return centers, radii

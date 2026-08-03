# Can't Be Late: spot scheduling under a hard deadline

You are evolving `solution.py`, a scheduling strategy for a long batch
job. Each simulator tick your `_step()` chooses SPOT (cheap, can vanish),
ON_DEMAND (roughly 3x the price, always available), or NONE (wait, free).
The job must finish before its deadline: the simulator enforces this as a
hard constraint, and a missed deadline on any simulation fails the whole
submit. The official metric is **mean dollar cost** across 12 simulations
(4 real spot-availability traces x 3 deadline tightnesses: 48h of work
against 52h, 70h, and 92h deadlines, restart overhead 0.02h). Lower is
better.

This is the ADRS benchmark task from the Can't Be Late paper, ported from
the optimize_anything artifact, where their evolved strategy claims 7.8%
savings over exactly the seed you are starting from. The kernel also runs
a hidden test split: same configs, four held-out traces from the same
zone, scored kernel-only.

## The contract

`solution.py` must define a `Strategy` subclass with a unique `NAME`, a
`_step(self, last_cluster_type, has_spot) -> ClusterType` method, and the
`_from_args` classmethod (keep the seed's). State available on self and
self.env:

- `self.task_duration`, `self.task_done_time` (list of completed chunks)
- `self.deadline`, `self.restart_overhead` (seconds)
- `self.env.elapsed_seconds`
- `has_spot`: whether spot capacity exists this tick
- switching clusters pays the restart overhead; preemptions do too

Everything must be deterministic. Your own sandbox is yours: write, run,
and install anything there; only submit is scored.

## Your workspace kit

- `sim/` is the full simulator with the 4 val traces. `local_eval.py`
  scores you exactly like the official eval and costs nothing. You can
  also invoke `sim/main.py` directly with your own flags.

Checkpoint with `POST $AR_API_URL/submit` (`{"notes": "..."}`), poll
`$AR_API_URL/submit/<id>` until `scored` or `failed`.
`GET $AR_API_URL/history` shows all past attempts.

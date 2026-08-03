# Can't Be Late: spot scheduling under a hard deadline

You are evolving `solution.py`, a scheduling strategy for a long batch
job. Each simulator tick your `_step()` chooses SPOT (cheap, can vanish),
ON_DEMAND (roughly 3x the price, always available), or NONE (wait, free).
The job must finish before its deadline: the simulator enforces this as a
hard constraint, and the official metric is **mean dollar cost** across
12 simulations (4 real spot-availability traces x 3 deadline tightnesses:
48h of work against 52h, 70h, and 92h deadlines, restart overhead 0.02h).
Lower is better.

This is the ADRS benchmark task from the Can't Be Late paper, ported from
the optimize_anything artifact, where their evolved strategy claims 7.8%
savings over exactly the seed you are starting from. The kernel also runs
a hidden test split (same configs, four held-out traces from the same
zone); tuning to val-trace quirks will show as val-test divergence.

## The contract

`solution.py` must define a `Strategy` subclass with a unique `NAME`, a
`_step(self, last_cluster_type, has_spot) -> ClusterType` method, and the
`_from_args` classmethod (keep the seed's). Useful state available on
self and self.env:

- `self.task_duration`, `self.task_done_time` (list of completed chunks)
- `self.deadline`, `self.restart_overhead` (seconds)
- `self.env.elapsed_seconds`
- `has_spot`: whether spot capacity exists this tick
- switching clusters pays the restart overhead; preemptions do too

## Your workspace kit

- `sim/` is the full simulator with the 4 val traces. `local_eval.py`
  scores you exactly like the official eval, free. You can also invoke
  `sim/main.py` directly to test single traces or print verbose output.
- The failure mode that matters: a strategy that misses a deadline on
  any simulation fails the whole submit. Safety margin logic is not
  optional; the game is how little safety you can pay for.

## Where the savings live

The seed switches to on-demand as soon as the naive check triggers and
never comes back to spot. Better strategies reason about spot outage
statistics (waiting through short gaps instead of panicking), track how
much slack remains versus how much overhead a switch costs, and adapt
the safety threshold as the deadline approaches.

Checkpoint with `POST $AR_API_URL/submit` (`{"notes": "..."}`), poll
`$AR_API_URL/submit/<id>`, and read history before repeating an idea.

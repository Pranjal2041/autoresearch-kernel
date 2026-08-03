"""The candidate: a spot-vs-on-demand scheduling strategy.

This seed is the optimize_anything paper's baseline: greedy spot use with
a deadline-safety check that switches to on-demand when time gets tight.
The simulator calls _step() once per tick; return which cluster to run
on. Missing the deadline fails the eval, so safety is a hard constraint
and cost is the objective.
"""

import math

from sky_spot.strategies.strategy import Strategy
from sky_spot.utils import ClusterType


class EvolveSingleRegionStrategy(Strategy):
    NAME = 'evolve_single_region'

    def __init__(self, args):
        super().__init__(args)

    def reset(self, env, task):
        super().reset(env, task)

    def _step(self, last_cluster_type: ClusterType, has_spot: bool) -> ClusterType:
        env = self.env

        # Task completion check
        remaining_task_time = self.task_duration - sum(self.task_done_time)
        if remaining_task_time <= 1e-3:
            return ClusterType.NONE

        # Remaining wall-clock time until the deadline
        remaining_time = self.deadline - env.elapsed_seconds

        # Deadline safety: if we might not fit a restart, go on-demand
        if remaining_task_time + self.restart_overhead >= remaining_time:
            return ClusterType.ON_DEMAND

        # Greedy: spot when available, otherwise wait
        if has_spot:
            return ClusterType.SPOT
        return ClusterType.NONE

    @classmethod
    def _from_args(cls, parser):
        args, _ = parser.parse_known_args()
        return cls(args)

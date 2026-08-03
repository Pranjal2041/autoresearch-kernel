# Provenance

The "Can't Be Late" spot-scheduling problem from the ADRS benchmark,
ported from the optimize_anything artifact:
https://github.com/gepa-ai/optimize-anything-artifact
(`acm_cais_artifact_evaluation/domains/cloud_scheduling/can_be_late`).
Their claim: 7.8% cost savings over the deadline-check baseline, topping
the ADRS leaderboard. The seed strategy here is their INITIAL_PROGRAM
verbatim.

`eval/simulator/` is the sky_spot simulator vendored from that artifact
(originally from the Can't Be Late paper, NSDI '24), with two patches
mirroring the stub their own `main.py` uses: the top-level wandb import
in `simulate.py` and the lazy one inside `utils.py:wandb_log` are both
optional. The second one only surfaces in a clean environment, since
`wandb_log` runs on every simulation tick. Traces are 8 of the real spot-availability
traces from their `real_traces.tar.gz` (us-west-2b_k80_1, random_start),
chosen after probing for signal: the us-west-2a samples have continuous
spot availability and carry none. Splits: val traces [0, 8, 33, 61],
test traces [9, 20, 42, 99], each crossed with deadlines 52/70/92h at
0.02h restart overhead.

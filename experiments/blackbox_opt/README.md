# Provenance

Ported from the optimize_anything paper's blackbox domain (Appendix B):
https://github.com/gepa-ai/optimize-anything-artifact
(`acm_cais_artifact_evaluation/domains/blackbox`). Their claim on this
task: evolved solvers beat Optuna on 7 of the 10 hardest EvalSet
problems at a 2000-evaluation budget.

`eval/evalset.py` (and the copy in `seed/`) is SigOpt's evalset
benchmark, vendored verbatim via the artifact above. The 10 problems are
their hardest-10 selection (indices 9, 10, 24, 31, 38, 45, 46, 51, 53,
54), split here 5 val / 5 test with related families straddling the
split.

Differences from their setup: our harness scores from its own call log
(never the solver's report), aggregates as mean normalized regret
against a seeded random baseline, and holds out the test half kernel-side.

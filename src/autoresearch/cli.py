"""The `ark` CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .events import EventLog
from .experiment import ExperimentError, load_experiment
from .tracking import Tracker


from . import help as help_texts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ark", add_help=True,
        description="autoresearch: run agents in a loop against a measurable objective",
        epilog="Run `ark help` for the full overview, `ark help <command|concept>` for details.",
    )
    sub = parser.add_subparsers(dest="command")

    def cmd(name: str, **kwargs) -> argparse.ArgumentParser:
        return sub.add_parser(
            name,
            help=_ONE_LINERS[name],
            description=help_texts.COMMANDS[name],
            formatter_class=argparse.RawDescriptionHelpFormatter,
            **kwargs,
        )

    p = cmd("validate")
    p.add_argument("experiment")

    p = cmd("run")
    p.add_argument("experiment")
    p.add_argument("--agent", required=True, action="append",
                   help="agent name (under ./agents) or path; repeat for parallel agents")
    p.add_argument("--runner", default="local", help="local | apple | docker (default: local)")
    p.add_argument("--name", default=None, help="run name (default: timestamped)")
    p.add_argument("--runs-dir", default="runs", help="parent directory for runs (default: ./runs)")
    p.add_argument("--resume", action="store_true", help="continue an existing run of this name")
    p.add_argument("--agent-env", action="append", default=[], metavar="KEY=VALUE",
                   help="override agent env vars (repeatable)")

    p = cmd("history")
    p.add_argument("run_dir")
    p.add_argument("--json", action="store_true", help="emit full records as JSON")

    p = cmd("best")
    p.add_argument("run_dir")

    p = cmd("diff")
    p.add_argument("run_dir")
    p.add_argument("a", type=int, help="base submit id")
    p.add_argument("b", type=int, help="target submit id")

    p = cmd("watch")
    p.add_argument("run_dir", help="a run directory, or a parent directory of runs")
    p.add_argument("--port", type=int, default=8722, help="port on 127.0.0.1 (default: 8722)")

    p = cmd("auth")
    p.add_argument("agent", help="agent name (under ./agents) or path")
    p.add_argument("--runner", default="apple", help="apple | docker (default: apple)")
    p.add_argument("--base", default="python:3.12-slim", help="base image for the login shell")
    p.add_argument("--shell", action="store_true",
                   help="open the interactive login shell even if seeding succeeded")

    p = cmd("init")
    p.add_argument("path", help="directory to create, e.g. experiments/my_experiment")

    p = cmd("list")
    p.add_argument("what", nargs="?", default="all",
                   choices=["all", "runs", "experiments", "agents", "runners"])
    p.add_argument("--runs-dir", default="runs")

    p = cmd("help")
    p.add_argument("topic", nargs="?", default=None,
                   help="a command or concept; omit for the overview")

    return parser


_ONE_LINERS = {
    "init": "Scaffold a new experiment folder",
    "list": "List runs, experiments, agents, and runners",
    "validate": "Check an experiment folder against the schema",
    "run": "Run an experiment with an agent on a runner",
    "watch": "Serve the web dashboard for a run or a runs directory",
    "history": "Print the submit table of a run",
    "best": "Print the best submit record of a run",
    "diff": "Show what changed between two submits",
    "auth": "Set up an agent's credentials for container runners",
    "help": "Detailed documentation for a command or concept",
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command is None:
        print(help_texts.OVERVIEW, end="")
        return 0
    try:
        return COMMANDS[args.command](args)
    except BrokenPipeError:  # e.g. `ark diff ... | head`
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


def cmd_init(args) -> int:
    from .scaffold import init_experiment
    try:
        root = init_experiment(args.path)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"created {root}/")
    print("  experiment.yaml   objective, environment, budgets (edit the TODOs)")
    print("  rules.md          what the agent is told")
    print("  seed/solution.py  the initial candidate")
    print("  eval/run.py       the scorer")
    print()
    print("next:")
    print(f"  ark validate {root}")
    print(f"  ark run {root} --agent random-search   # green pipeline before real agents")
    return 0


def cmd_list(args) -> int:
    import yaml
    from .dashboard import _list_run_dirs, run_card
    from .registry import RUNNERS

    def read_meta(path: Path, fname: str) -> tuple[str, str]:
        try:
            raw = yaml.safe_load((path / fname).read_text()) or {}
            return raw.get("name", path.name), " ".join(str(raw.get("description", "")).split())
        except Exception:
            return path.name, "(unreadable)"

    def section(title: str) -> None:
        print(f"\n{title}")

    what = args.what
    if what in ("all", "runs"):
        section("runs")
        run_dirs = _list_run_dirs(Path(args.runs_dir).resolve()) if Path(args.runs_dir).is_dir() else []
        if not run_dirs:
            print(f"  (none under {args.runs_dir}/)")
        for d in run_dirs:
            c = run_card(d)
            status = "live" if c["live"] else (c["finished_reason"] or "idle")
            best = f"{c['metric']}={c['best']}" if c["best"] is not None else "no score"
            print(f"  {c['name']:<24} {c['experiment']:<22} {c['agent']:<12} "
                  f"{c['runner']:<7} {c['submits']:>3} submits  {best}  [{status}]")
    if what in ("all", "experiments"):
        section("experiments")
        folders = sorted(p.parent for p in Path("experiments").glob("*/experiment.yaml"))
        if not folders:
            print("  (none under experiments/)")
        for f in folders:
            name, desc = read_meta(f, "experiment.yaml")
            print(f"  {f.name:<24} {desc[:76]}")
    if what in ("all", "agents"):
        from .registry import PACKAGED_AGENTS
        section("agents")
        local = {p.parent.name: p.parent for p in Path("agents").glob("*/agent.yaml")}
        bundled = ({p.parent.name: p.parent for p in PACKAGED_AGENTS.glob("*/agent.yaml")}
                   if PACKAGED_AGENTS.is_dir() else {})
        if not local and not bundled:
            print("  (none)")
        for name in sorted({**bundled, **local}):
            folder = local.get(name, bundled.get(name))
            _, desc = read_meta(folder, "agent.yaml")
            origin = "local" if name in local else "bundled"
            print(f"  {name:<24} [{origin}] {desc[:68]}")
    if what in ("all", "runners"):
        section("runners")
        for name, desc in RUNNERS.items():
            print(f"  {name:<24} {desc}")
    return 0


def cmd_help(args) -> int:
    if args.topic is None:
        print(help_texts.OVERVIEW, end="")
        return 0
    text = help_texts.lookup(args.topic)
    if text is None:
        known = sorted([*help_texts.COMMANDS, *help_texts.CONCEPTS, "design"])
        print(f"no help for '{args.topic}'. Topics: {', '.join(known)}", file=sys.stderr)
        return 1
    print(text, end="")
    return 0


def cmd_validate(args) -> int:
    try:
        exp = load_experiment(args.experiment)
    except ExperimentError as e:
        print(f"INVALID: {args.experiment}")
        for problem in e.problems:
            print(f"  - {problem}")
        return 1
    print(f"OK: {exp.name}")
    print(f"  objective: {exp.objective.direction} {exp.objective.metric} ({exp.objective.mode} mode)")
    print(f"  signature: {', '.join(f.name + ':' + f.type for f in exp.signature) or '(empty)'}")
    print(f"  environment: {exp.environment.base}  resources: {exp.environment.resources}")
    stop = []
    if exp.stop.max_submits: stop.append(f"max_submits={exp.stop.max_submits}")
    if exp.stop.max_hours: stop.append(f"max_hours={exp.stop.max_hours}")
    if exp.stop.target is not None: stop.append(f"target={exp.stop.target}")
    print(f"  stop: {', '.join(stop)}")
    return 0


def cmd_run(args) -> int:
    from .run import start_run
    overrides = {}
    for item in args.agent_env:
        key, sep, value = item.partition("=")
        if not sep:
            print(f"error: --agent-env expects KEY=VALUE, got '{item}'", file=sys.stderr)
            return 1
        overrides[key] = value
    try:
        start_run(
            args.experiment,
            agent=args.agent,
            runner_name=args.runner,
            run_name=args.name,
            runs_root=args.runs_dir,
            resume=args.resume,
            agent_env=overrides,
        )
    except (ExperimentError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _records(run_dir: str):
    log = EventLog(Path(run_dir) / "events.jsonl")
    records = log.replay_records()
    if not records:
        print(f"no submits recorded in {run_dir}", file=sys.stderr)
    return [records[i] for i in sorted(records)]


def cmd_history(args) -> int:
    records = _records(args.run_dir)
    if not records:
        return 1
    if args.json:
        print(json.dumps([r.to_dict() for r in records], indent=2))
        return 0
    print(f"{'id':>4}  {'status':<12} {'metric':>12}  {'test':>12}  {'best':>4}  {'commit':<8}  note")
    for r in records:
        metric = f"{r.metric.value:.6g}" if r.metric else "-"
        test = f"{r.test_metric.value:.6g}" if r.test_metric else "-"
        note = str(r.payload.get("notes", ""))[:60]
        if r.status == "failed":
            note = (r.error or "")[:60]
        print(f"{r.submit_id:>4}  {r.status:<12} {metric:>12}  {test:>12}  {'*' if r.best_so_far else '':>4}  {r.commit[:8]}  {note}")
    return 0


def cmd_best(args) -> int:
    records = [r for r in _records(args.run_dir) if r.status == "scored"]
    if not records:
        return 1
    # Direction is not recorded per event; best_so_far marks carry it.
    best = [r for r in records if r.best_so_far]
    if best:
        r = best[-1]
        print(json.dumps(r.to_dict(), indent=2))
        return 0
    return 1


def cmd_diff(args) -> int:
    run_dir = Path(args.run_dir)
    log = EventLog(run_dir / "events.jsonl")
    records = log.replay_records()
    for sid in (args.a, args.b):
        if sid not in records:
            print(f"error: no submit {sid} in {run_dir}", file=sys.stderr)
            return 1
    tracker = Tracker(run_dir / "repo.git", run_dir / "workspace", 5.0, [])
    print(tracker.diff(records[args.a].commit, records[args.b].commit))
    return 0


def cmd_watch(args) -> int:
    from .dashboard import serve_dashboard
    return serve_dashboard(Path(args.run_dir), args.port)


def cmd_auth(args) -> int:
    """Credential setup, following the simulation-kernel pattern: a host
    directory holds the agent CLI's credentials and is mounted into every
    agent container. Seed it from existing host credentials when possible;
    otherwise drop into a one-time interactive login shell inside the
    container image that the agent will actually run in."""
    import shutil
    from .registry import resolve_agent, resolve_runner
    from .runners.base import Mount

    agent = resolve_agent(args.agent, Path.cwd())
    if not agent.mounts:
        print(f"agent '{agent.name}' declares no credential mounts; nothing to set up")
        return 0
    mounts = [Mount(Path(m["source"]).expanduser(), m["target"], m.get("mode", "rw"))
              for m in agent.mounts]
    for mount in mounts:
        mount.source.mkdir(parents=True, exist_ok=True)

    # Seed from existing host credentials (the migrate_credentials trick):
    # often no interactive login is needed at all.
    seeded = []
    if agent.auth_seed:
        host_dir = Path(agent.auth_seed.get("host", "")).expanduser()
        for name in agent.auth_seed.get("files", []):
            source, dest = host_dir / name, mounts[0].source / name
            if source.is_file() and not dest.exists():
                shutil.copy2(source, dest)
                seeded.append(name)
    if seeded:
        print(f"seeded {', '.join(seeded)} from {agent.auth_seed['host']} into {mounts[0].source}")
    if seeded and not args.shell:
        print("done: agent containers will mount these credentials. "
              "Use --shell if you need an interactive login instead.")
        return 0

    runner = resolve_runner(args.runner)
    print(f"building the {agent.name} agent layer on {args.base} ({args.runner})...")
    image = runner.build_layer(args.base, agent.image_setup, cache_key=f"{agent.name}-auth")
    print("=" * 60)
    print(f"  Dropping you into a shell inside a {args.runner} container.")
    print(f"  Credentials persist in {mounts[0].source} and are")
    print("  mounted into every future agent container.")
    if agent.auth_help:
        for line in agent.auth_help.strip().splitlines():
            print(f"  {line}")
    print("  Then: exit  (to leave the container)")
    print("=" * 60)
    runner.interactive_shell(image, mounts)
    print(f"done: credentials stored in {mounts[0].source}")
    return 0


COMMANDS = {
    "init": cmd_init,
    "list": cmd_list,
    "validate": cmd_validate,
    "run": cmd_run,
    "history": cmd_history,
    "best": cmd_best,
    "diff": cmd_diff,
    "watch": cmd_watch,
    "auth": cmd_auth,
    "help": cmd_help,
}


if __name__ == "__main__":
    raise SystemExit(main())

"""Portable CLI. Simulation and verification run without cloud credentials."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from .engine import create_capsule, json_bytes, search, verify_capsule, write_json
from .schema import ScenarioSpec
from .simulator import simulate


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'platform':
        from .platform.cli import main as platform_main
        return platform_main(argv[1:])
    parser = argparse.ArgumentParser(prog="worldfuzz")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser('platform', help='Versioned task service, HTTP, SDK and MCP')
    search_cmd = sub.add_parser("search", help="Search the bounded synthetic simulator")
    search_cmd.add_argument("--strategy", choices=["random", "nemotron"], default="random")
    search_cmd.add_argument("--seed", type=int, default=42)
    search_cmd.add_argument("--budget", type=int, default=24, help="Search rollout limit; validation counts are separate")
    search_cmd.add_argument("--max-model-calls", type=int,
                            help="Model attempt cap, including failures; default ceil(budget / 8). No retries.")
    search_cmd.add_argument("--out", type=Path, required=True)
    demo = sub.add_parser("demo", help="Replay a documented regression fixture, not an AI discovery")
    demo.add_argument("--out", type=Path, required=True)
    verify = sub.add_parser("verify", help="Check hashes, recompute monitors, and rerun evidence")
    verify.add_argument("capsule", type=Path)
    pilot = sub.add_parser("pilot", help="Run the fixed offline tuning pilot; no provider requests")
    pilot.add_argument("--out", type=Path, required=True)
    pilot_verify = sub.add_parser("pilot-verify", help="Replay and verify recorded offline tuning trials")
    pilot_verify.add_argument("directory", type=Path)
    dashboard = sub.add_parser("dashboard", help="Open the local, zero-provider evidence dashboard")
    dashboard.add_argument("--out", type=Path, default=Path("artifacts/dashboard"))
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--open", action="store_true", dest="open_browser")
    experiment_verify = sub.add_parser("experiment-verify", help="Verify an interactive local paired search")
    experiment_verify.add_argument("directory", type=Path)
    regression_template = sub.add_parser("regression-template", help="Write an editable external navigation comparison project")
    regression_template.add_argument("--out", type=Path, required=True)
    regression = sub.add_parser("regression", help="Compare two configurations on the pinned external navigation planner")
    regression.add_argument("--project", type=Path, required=True)
    regression.add_argument("--out", type=Path, required=True)
    regression_verify = sub.add_parser("regression-verify", help="Validate evidence and rerun the selected external comparison")
    regression_verify.add_argument("directory", type=Path)
    replay = sub.add_parser("replay")
    replay.add_argument("scenario", type=Path)
    replay.add_argument("--policy", choices=["baseline", "hardened"], default="baseline")
    replay.add_argument("--out", type=Path, required=True)
    render = sub.add_parser("render", help="Render exact stored traces; requires Pillow")
    render.add_argument("capsule", type=Path)
    render.add_argument("--out", type=Path, required=True)
    preflight = sub.add_parser("preflight", help="Inspect configuration, or explicitly probe real inference")
    preflight.add_argument("--live", action="store_true", help="Make one real billable inference call after credit setup")
    preflight.add_argument("--out", type=Path, help="Save a new secret-free verification report")
    sub.add_parser("models", help="Read the live Token Factory model catalog")
    doctor = sub.add_parser("doctor", help="Report runtime/configuration without revealing credentials")
    doctor.add_argument("--catalog", action="store_true", help="Read the live catalog; no inference")
    doctor.add_argument("--out", type=Path, help="Save a new secret-free diagnostic report")
    connection = sub.add_parser("connection", help="Encrypted connection transfer between your runtimes")
    connection.add_argument("connection_args", nargs=argparse.REMAINDER)
    data = sub.add_parser("data", help="Import, inspect and export reusable robotics data")
    data.add_argument("data_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "data":
            from .data_cli import main as data_main
            return data_main(args.data_args)
        if args.command == "connection":
            from .connection import main as connection_main
            return connection_main(args.connection_args)
        # Validate report output before any API request. Never overwrite prior evidence.
        report_file = None
        if args.command in ("preflight", "doctor") and args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            report_file = args.out.open("x", encoding="utf-8")
        try:
            return _run(args, report_file)
        finally:
            if report_file:
                report_file.close()
    except (ValueError, OSError, RuntimeError, ImportError, KeyError) as error:
        return _failure(args, error)


def _run(args, report_file=None):
    try:
        if args.command == "dashboard":
            from .dashboard import serve
            if not 1 <= args.port <= 65535:
                raise ValueError("Port must be between 1 and 65535")
            return serve(args.out, args.port, args.open_browser)
        elif args.command == "experiment-verify":
            from .experiment import verify_experiment
            outcome = verify_experiment(args.directory)
        elif args.command == "regression-template":
            from .regression import default_project
            with args.out.open("xb") as target:
                target.write(json_bytes(default_project()))
            outcome = {"status": "CREATED", "project": str(args.out)}
        elif args.command == "regression":
            from .experiment import read_json
            from .regression import new_regression, run_regression
            new_regression(args.out, read_json(args.project))
            outcome = run_regression(args.out)
        elif args.command == "regression-verify":
            from .regression import verify_regression
            outcome = verify_regression(args.directory)
        elif args.command == "search":
            # Reserve writable evidence storage before any paid-capable work.
            if args.out.is_symlink() or (args.out.exists() and
                    (not args.out.is_dir() or any(args.out.iterdir()))):
                raise ValueError("Choose an empty output directory")
            args.out.mkdir(parents=True, exist_ok=True)
            planner = None
            if args.strategy == "nemotron":
                from .nebius import NemotronPlanner
                planner = NemotronPlanner()
            result = search(args.seed, args.budget, planner,
                            checkpoint=_checkpoint_writer(args.out),
                            max_model_calls=args.max_model_calls)
            outcome = create_capsule(args.out, result, allow_campaign_journal=True)
        elif args.command == "demo":
            scenario = ScenarioSpec(seed=42, human_crossing_time_s=4.0,
                                    robot_speed_mps=1.0, sensor_delay_ms=1200)
            run = simulate(scenario, "baseline")
            outcome = create_capsule(args.out, {
                "strategy": "documented_regression_fixture", "target": "human_distance",
                "seed": 42, "budget": 1, "executed_runs": 1, "elapsed_seconds": None,
                "history": [{"index": 1, "scenario": scenario.as_dict(), "summary": run["summary"]}],
                "model_calls": [], "found": run})
        elif args.command == "verify":
            outcome = verify_capsule(args.capsule)
        elif args.command == "pilot":
            from .pilot import run_pilot
            outcome = run_pilot(args.out)
        elif args.command == "pilot-verify":
            from .pilot import verify_pilot
            outcome = verify_pilot(args.directory)
        elif args.command == "replay":
            if args.out.exists():
                raise ValueError("Replay output already exists")
            scenario = ScenarioSpec.from_dict(json.loads(args.scenario.read_text()))
            run = simulate(scenario, args.policy)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.out, run)
            outcome = {"trace_sha256": run["trace_sha256"], "summary": run["summary"], "path": str(args.out)}
        elif args.command == "render":
            from .render import render_comparison
            outcome = render_comparison(args.capsule, args.out)
        elif args.command == "preflight":
            from .nebius import preflight
            outcome = preflight(live=args.live)
        elif args.command == "models":
            from .nebius import list_models
            outcome = {"models": list_models()}
        elif args.command == "doctor":
            from .doctor import diagnose
            outcome = diagnose(catalog=args.catalog)
        if report_file:
            json.dump(outcome, report_file, indent=2, allow_nan=False)
            report_file.write("\n")
        print(json.dumps(outcome, indent=2, allow_nan=False))
        if args.command == "preflight" and outcome["status"] not in ("passed", "configuration_present"):
            return 2
        if args.command == "doctor" and outcome["status"] == "blocked":
            return 2
        if args.command == "search":
            if result["status"] == "CANCELLED":
                return 130
            if result["status"] in ("FAILED", "MODEL_BUDGET_EXHAUSTED"):
                return 2
        if args.command == "pilot" and outcome["status"] != "COMPLETED":
            return 130 if outcome["status"] == "CANCELLED" else 2
        if args.command == "regression":
            if outcome["status"] != "COMPLETED":
                return 130 if outcome["status"] == "CANCELLED" else 1
            return 2 if outcome["counts"]["regression_pairs"] else 0
        if args.command == "regression-verify" and outcome["status"] != "VERIFIED":
            return 130 if outcome["status"] == "CANCELLED" else 1
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"status": "CANCELLED", "message":
                          "Interrupted. Existing campaign evidence is preserved; no automatic retry."}),
              file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, ImportError, KeyError) as error:
        return _failure(args, error, report_file)


def _checkpoint_writer(directory):
    """Persist each boundary before more work; replacing a snapshot is atomic.

    A pending request after process termination has unknown usage. This writer
    deliberately cannot reopen a prior campaign or resume paid work.
    """
    journal = Path(directory) / "campaign.json"
    claimed = False

    def checkpoint(record):
        nonlocal claimed
        data = json_bytes(record)
        if not claimed:
            with journal.open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            claimed = True
            return
        descriptor, temporary = tempfile.mkstemp(prefix="campaign-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, journal)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    return checkpoint


def _failure(args, error, report_file=None):
    failure = {"status": "ERROR", "message": str(error)}
    if hasattr(error, "code"):
        failure["error_code"] = error.code
        failure["provenance"] = error.provenance
        if args.command == "search" and not args.out.exists():
            args.out.mkdir(parents=True)
            write_json(args.out / "failure.json", failure)
    if report_file:
        json.dump(failure, report_file, indent=2, allow_nan=False)
        report_file.write("\n")
    print(json.dumps(failure), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

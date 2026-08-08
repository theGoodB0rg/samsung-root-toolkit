"""Command-line interface for SRTK.

Subcommands::

    srtk init <job-id>       create a job workspace + state
    srtk run [phases]        run one or more phases (default: all)
    srtk status              show per-phase state for a job
    srtk runbook [phase]     print the operator/client runbook
    srtk bootstrap           print host-tooling setup instructions
    srtk verify-only         run the read-only preflight + device_info phases

Run the whole pipeline against a simulated device (no phone needed)::

    python -m srtk --root . --sim --yes run all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .context import Config, RunContext
from .core.errors import ErrorCode, SrtkError
from .core.state import RunState
from .core.transport import (
    Adb,
    CommandRunner,
    DeviceDetector,
    HostRunner,
    Scrcpy,
)
from .phases import ORDER, PHASES, plan_for, validate_targets

DEFAULT_ROOT = Path(__file__).resolve().parents[2]

_PLAN_ACTIONS = {"run": "run", "skip": "skip", "block": "BLOCK"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="srtk",
        description=(
            "Remote Samsung SM-A325F bootloader unlock + Magisk root + Play "
            "Integrity toolkit. Run `srtk runbook` for the operator/client "
            "runbook, `srtk run all --sim` for a simulated dry pipeline."
        ),
    )
    parser.add_argument("--root", type=Path, default=None,
                        help="toolkit root (default: repo directory)")
    parser.add_argument("--job", default="job-default",
                        help="job workspace under runs/")
    parser.add_argument("--sim", action="store_true",
                        help="run against a simulated device")
    parser.add_argument("--yes", action="store_true",
                        help="auto-confirm prompts (scripted mode)")

    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a new job workspace")
    init.add_argument("job_id")

    run = sub.add_parser("run", help="run phases (default: all)")
    run.add_argument("phases", nargs="*", help="phase names, or 'all'")
    run.add_argument("--dry-run", action="store_true",
                     help="stop before mutating actions (download/flash/unlock)")
    run.add_argument("--resume", action="store_true",
                     help="skip phases already passed")
    run.add_argument("--plan", action="store_true",
                     help="print the execution plan and exit")

    sub.add_parser("status", help="show phase status for a job")
    sub.add_parser("bootstrap", help="print host-tooling setup instructions")
    sub.add_parser("verify-only", help="run the read-only preflight + device_info phases")

    runbook = sub.add_parser("runbook", help="print operator/client runbook")
    runbook.add_argument("phase", nargs="?",
                         help="print one phase's runbook only")

    return parser


def _build_ctx(
    root: Path,
    workspace: Path,
    job_id: str,
    sim: bool,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> RunContext:
    if sim:
        from .sim_device import SimDevice, SimRunner

        runner: CommandRunner = SimRunner(SimDevice())
        adb = Adb(runner, "adb")
        scrcpy = Scrcpy(runner, "scrcpy")
    else:
        runner = HostRunner()
        adb = Adb(runner, root / "tools" / "bin" / "adb.exe")
        scrcpy = Scrcpy(runner, root / "tools" / "bin" / "scrcpy.exe")

    config = Config(root=root, assume_yes=assume_yes)
    ctx = RunContext(
        job_id=job_id,
        workspace=workspace,
        state=RunState(workspace / "state.json"),
        runner=runner,
        adb=adb,
        detector=DeviceDetector(adb, runner),
        scrcpy=scrcpy,
        config=config,
        sim=sim,
        dry_run=dry_run,
    )
    ctx.ensure_dirs()
    return ctx


def _cmd_init(root: Path, args: argparse.Namespace) -> int:
    ws = root / "runs" / args.job_id
    ctx = _build_ctx(root, ws, args.job_id, sim=False)
    ctx.state.data["job_id"] = args.job_id
    ctx.state.save()
    print(f"initialized job workspace: {ws}")
    print(f"state file: {ctx.state_path}")
    return 0


def _cmd_run(root: Path, args: argparse.Namespace) -> int:
    ws = root / "runs" / args.job
    sim = args.sim or (args.command == "verify-only" and args.sim)
    ctx = _build_ctx(
        root, ws, args.job,
        sim=sim,
        dry_run=getattr(args, "dry_run", False),
        assume_yes=args.yes,
    )

    if args.command == "verify-only":
        targets = ["preflight", "device_info"]
    else:
        targets = list(args.phases) or ["all"]
        if "all" in targets:
            targets = ORDER
        validate_targets(targets)

    plan = plan_for(ctx.state, targets, resume=getattr(args, "resume", False))
    if getattr(args, "plan", False):
        return _print_plan(plan)

    for name, action in plan:
        if action == "skip":
            print(f"[{name}] already passed; skipping")
            continue
        if action == "block":
            print(f"[{name}] blocked by a failed prerequisite", file=sys.stderr)
            return ErrorCode.PHASE_PREREQ_FAIL.value[0]
        try:
            PHASES[name](ctx).run()
        except SrtkError as exc:
            print(f"[{name}] FAILED: {exc}", file=sys.stderr)
            return exc.exit_code
    return 0


def _print_plan(plan: list[tuple[str, str]]) -> int:
    print(f"plan ({len(plan)} steps):")
    for name, action in plan:
        print(f"  {name:<18} {_PLAN_ACTIONS[action]}")
    return 0


def _cmd_status(root: Path, args: argparse.Namespace) -> int:
    state_path = root / "runs" / args.job / "state.json"
    if not state_path.exists():
        print(f"no state for job '{args.job}' (run `srtk init <job-id>` first)")
        return 1
    state = RunState(state_path)
    print(f"job: {state.get('job_id') or args.job}")
    print(f"created: {state.get('created_utc') or 'n/a'}")
    print("phases:")
    for name in ORDER:
        rec = (state.data.get("phases") or {}).get(name) or {}
        status = rec.get("status", "pending")
        notes = f" — {rec['notes']}" if rec.get("notes") else ""
        print(f"  {name:<18} {status}{notes}")
    for key in ("unlock_report", "flash", "play_integrity"):
        value = state.get(key)
        if value:
            print(f"{key}: {value}")
    print(f"evidence bundle: {state.get('evidence_bundle') or 'not built'}")
    return 0


def _cmd_runbook(args: argparse.Namespace) -> int:
    from .runbook_gen import full_runbook, phase_runbook

    if args.phase:
        if args.phase not in PHASES:
            print(f"unknown phase '{args.phase}'; known: {', '.join(ORDER)}",
                  file=sys.stderr)
            return 1
        print(phase_runbook(args.phase))
    else:
        print(full_runbook())
    return 0


def _cmd_bootstrap(root: Path) -> int:
    script = root / "scripts" / "bootstrap.ps1"
    if not script.exists():
        print(f"bootstrap script not found: {script}", file=sys.stderr)
        return 1
    print("Install host tooling from an elevated PowerShell:")
    print(f'  powershell -ExecutionPolicy Bypass -File "{script}"')
    print("Run scripts/driver_check.ps1 afterwards to diagnose adb/USB issues.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = (args.root or DEFAULT_ROOT).resolve()
    try:
        if args.command == "init":
            return _cmd_init(root, args)
        if args.command == "status":
            return _cmd_status(root, args)
        if args.command == "runbook":
            return _cmd_runbook(args)
        if args.command == "bootstrap":
            return _cmd_bootstrap(root)
        if args.command in ("run", "verify-only"):
            return _cmd_run(root, args)
        parser.print_help()
        return 0
    except SrtkError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

"""Command Line Interface for SUPERVISOR."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from supervisor.models import FindingStatus, LifecycleState, Severity
from supervisor.orchestrator import SupervisorOrchestrator
from supervisor.reporter import ReportGenerator
from supervisor.state import StateManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supervisor",
        description="SUPERVISOR: An AI Coding Supervision and Automatic Repair System",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Supervision command")

    # review
    review_p = subparsers.add_parser("review", help="Run SUPERVISOR review on current workspace")
    review_p.add_argument("--prompt", "-p", type=str, default="", help="Original task prompt")
    review_p.add_argument("--task-id", "-t", type=str, default=None, help="Explicit task ID")
    review_p.add_argument("--test-cmd", type=str, default=None, help="Explicit test command to run")
    review_p.add_argument("--report-only", action="store_true", help="Generate report without initiating repair handoff")
    review_p.add_argument("--max-retries", type=int, default=3, help="Max repair retry limit")

    # verify
    verify_p = subparsers.add_parser("verify", help="Verify corrections after coding agent repairs")
    verify_p.add_argument("--test-cmd", type=str, default=None, help="Explicit test command to run")

    # status
    subparsers.add_parser("status", help="Display current lifecycle state and active findings")

    # init
    init_p = subparsers.add_parser("init", help="Initialize a new supervision task session")
    init_p.add_argument("prompt", type=str, help="The task instructions/prompt")
    init_p.add_argument("--max-retries", type=int, default=3, help="Max repair retry limit")

    # reset
    subparsers.add_parser("reset", help="Reset supervisor session state")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        # Default behavior: run review with auto-repair handoff
        args.subcommand = "review"
        args.prompt = ""
        args.task_id = None
        args.test_cmd = None
        args.report_only = False
        args.max_retries = 3

    workspace_root = Path.cwd()
    state_mgr = StateManager(workspace_root)
    orchestrator = SupervisorOrchestrator(workspace_root)
    reporter = ReportGenerator(workspace_root)

    if args.subcommand == "init":
        state = state_mgr.initialize_task(args.prompt, max_retries=args.max_retries)
        print(f"Initialized SUPERVISOR task '{state.task.task_id}' with max_retries={state.max_retries}")
        return 0

    elif args.subcommand == "reset":
        state_mgr.initialize_task("Reset task", max_retries=3)
        state_mgr.transition_to(LifecycleState.IMPLEMENTING, "Session reset.")
        print("SUPERVISOR state has been reset.")
        return 0

    elif args.subcommand == "status":
        state = state_mgr.load()
        print(f"SUPERVISOR Status:")
        print(f"- State: {state.state.value}")
        print(f"- Cycle: {state.cycle} / {state.max_retries}")
        if state.task:
            print(f"- Task: {state.task.task_id} ({state.task.prompt[:60]}...)")
        if state.latest_report:
            print(f"- Latest Report: {state.latest_report.report_id}")
            print(f"- Material Findings: {len(state.latest_report.material_findings)}")
            for f in state.latest_report.material_findings:
                print(f"  [{f.severity.value}] {f.finding_id}: {f.description} ({f.file_path})")
        return 0

    elif args.subcommand == "review":
        report = orchestrator.execute_review_cycle(
            prompt=args.prompt if args.prompt else None,
            task_id=args.task_id,
            test_command=args.test_cmd,
            report_only=args.report_only,
        )
        md = reporter.to_markdown(report)
        print(md)
        return 0 if not report.has_material_issues else 1

    elif args.subcommand == "verify":
        report = orchestrator.execute_verification_cycle(
            test_command=args.test_cmd,
        )
        md = reporter.to_markdown(report)
        print(md)
        return 0 if not report.has_material_issues else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())


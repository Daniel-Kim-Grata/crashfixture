"""Run one synthetic worker or a deterministic conformance demo."""

from __future__ import annotations

import argparse
import sys

from .harness import Harness
from .model import Boundary, Scenario, Status
from .reporting import write_json, write_junit
from .workers import MODES


def worker_command(mode: str) -> list[str]:
    return [sys.executable, "-m", "crashfixture.workers", mode]


def demo_cases() -> list[tuple[str, Scenario, str, Status]]:
    cases = [("correct", Scenario(b), "kill", Status.PASS) for b in Boundary]
    cases += [
        ("duplicate", Scenario(Boundary.AFTER_EFFECT), "kill", Status.FAIL),
        ("forget_checkpoint", Scenario(Boundary.AFTER_CHECKPOINT), "kill", Status.FAIL),
        ("lose_task", Scenario(Boundary.BEFORE_DISPATCH), "kill", Status.FAIL),
        (
            "retry",
            Scenario(Boundary.BEFORE_DISPATCH, expected_terminal="failed", expected_effects=0),
            "kill",
            Status.PASS,
        ),
        ("retry_forever", Scenario(Boundary.BEFORE_DISPATCH), "kill", Status.FAIL),
    ]
    cases += [("correct", Scenario(b), "noop", Status.INCONCLUSIVE) for b in Boundary]
    cases += [("uninstrumented", Scenario(b), "kill", Status.INCONCLUSIVE) for b in Boundary]
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    single = subparsers.add_parser("run", help="one trial; exit 0=pass, 1=fail, 2=inconclusive")
    single.add_argument("--worker", choices=MODES, default="correct")
    single.add_argument("--boundary", choices=[b.value for b in Boundary], required=True)
    single.add_argument("--max-attempts", type=int, default=3)
    single.add_argument("--timeout", type=float, default=2.0)
    single.add_argument("--expected-terminal", choices=["succeeded", "failed"], default="succeeded")
    single.add_argument("--expected-effects", type=int, choices=[0, 1], default=1)
    single.add_argument("--fault-mode", choices=["kill", "noop"], default="kill")
    demo = subparsers.add_parser("demo", help="exit 0 only if all expected control verdicts match")
    for command in (single, demo):
        command.add_argument("--workdir", default="crashfixture-artifacts")
        command.add_argument("--json", dest="json_path")
        command.add_argument("--junit", dest="junit_path")
    args = parser.parse_args(argv)
    if args.action == "demo":
        cases = demo_cases()
    else:
        try:
            scenario = Scenario(
                Boundary(args.boundary),
                args.max_attempts,
                args.timeout,
                args.expected_terminal,
                args.expected_effects,
            )
        except ValueError as exc:
            parser.error(str(exc))
        cases = [(args.worker, scenario, args.fault_mode, Status.PASS)]
    harness = Harness(args.workdir)
    trials = []
    expected_match = True
    for mode, scenario, fault_mode, expected in cases:
        name = f"{mode}.{scenario.boundary.value}.{fault_mode}"
        result = harness.run(worker_command(mode), scenario, fault_mode=fault_mode)
        trials.append((name, result))
        expected_match &= result.status is expected
        print(f"{name}: {result.status.value} {','.join(result.reasons)}".rstrip())
    if args.json_path:
        write_json(args.json_path, trials)
    if args.junit_path:
        write_junit(args.junit_path, trials)
    if args.action == "demo":
        print(f"conformance: {'matched' if expected_match else 'MISMATCH'} ({len(trials)} trials)")
        return 0 if expected_match else 1
    return {Status.PASS: 0, Status.FAIL: 1, Status.INCONCLUSIVE: 2}[trials[0][1].status]

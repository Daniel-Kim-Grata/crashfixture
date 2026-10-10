"""Finite scenario and evidence types; no aggregate benchmark score."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Boundary(str, Enum):
    BEFORE_DISPATCH = "before_dispatch"
    AFTER_EFFECT = "after_effect"
    AFTER_CHECKPOINT = "after_checkpoint"


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Scenario:
    boundary: Boundary
    max_attempts: int = 3  # Total process launches, including the killed launch.
    timeout: float = 2.0  # Per process, not a timing-based injection trigger.
    expected_terminal: str = "succeeded"
    expected_effects: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.boundary, Boundary):
            raise ValueError("boundary must be a Boundary")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be an integer from 1 to 10")
        if isinstance(self.timeout, bool) or not math.isfinite(self.timeout):
            raise ValueError("timeout must be finite")
        if not 0 < self.timeout <= 30:
            raise ValueError("timeout must be in (0, 30] seconds")
        if self.expected_terminal not in {"succeeded", "failed"}:
            raise ValueError("expected_terminal must be succeeded or failed")
        if type(self.expected_effects) is not int or self.expected_effects not in {0, 1}:
            raise ValueError("this fixture supports zero or one expected synthetic effect")


@dataclass(frozen=True)
class Attempt:
    number: int
    token: str
    pid: int | None
    returncode: int | None
    events: tuple[str, ...]
    timed_out: bool = False
    error: str | None = None


@dataclass(frozen=True)
class Fault:
    boundary: str
    token: str
    pid: int
    sent_signal: int | None
    wait_returncode: int | None
    snapshot: dict[str, Any]
    snapshot_valid: bool

    @property
    def witnessed(self) -> bool:
        # SIGKILL=9 on the supported POSIX platforms. A claimed injection alone
        # cannot satisfy this gate; waitpid evidence must match the exact child.
        return self.sent_signal == 9 and self.wait_returncode == -9 and self.snapshot_valid


class InconclusiveError(AssertionError):
    """Missing/invalid instrumentation is a CI error, never a passing skip."""


@dataclass(frozen=True)
class Result:
    scenario: Scenario
    status: Status
    reasons: tuple[str, ...]
    attempts: tuple[Attempt, ...]
    fault: Fault | None
    final_snapshot: dict[str, Any]
    artifact_dir: str

    def require_pass(self) -> Result:
        detail = f"crashfixture {self.status.value}: {', '.join(self.reasons)}"
        if self.status is Status.INCONCLUSIVE:
            raise InconclusiveError(detail)
        if self.status is Status.FAIL:
            raise AssertionError(detail)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, **asdict(self)}

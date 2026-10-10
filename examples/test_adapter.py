"""Executable integration pattern; no OpenHands dependency or network access."""

import sys
from pathlib import Path

import pytest

from crashfixture import Boundary, Scenario


@pytest.mark.parametrize("boundary", list(Boundary))
def test_subprocess_adapter(recovery, boundary):
    worker = Path(__file__).with_name("adapter_worker.py").resolve()
    result = recovery.run([sys.executable, str(worker)], Scenario(boundary))
    result.require_pass()
    assert result.fault.witnessed

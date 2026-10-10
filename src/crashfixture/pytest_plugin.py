"""Optional pytest integration. Importing the base package does not need pytest."""

from pathlib import Path

import pytest

from .harness import Harness


@pytest.fixture
def recovery(tmp_path: Path) -> Harness:
    """Isolated subprocess harness; call result.require_pass() to assert."""
    return Harness(tmp_path / "crashfixture")

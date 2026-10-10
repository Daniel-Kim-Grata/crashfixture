"""Run the canonical local checks, sequentially; no installs or publication."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    environment = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    checks = [
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "crashfixture.pytest_plugin",
            "-q",
            "--junitxml=artifacts/pytest.xml",
        ],
        [sys.executable, str(ROOT / "scripts" / "build_release.py")],
    ]
    for command in checks:
        print("check:", " ".join(command[1:]), flush=True)
        completed = subprocess.run(command, cwd=ROOT, env=environment, check=False, timeout=120)
        if completed.returncode:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

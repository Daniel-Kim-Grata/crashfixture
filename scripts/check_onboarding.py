"""Check a wheel's console command and pytest fixture outside the source checkout.

Use a trusted interpreter with the wheel and pytest installed. This script never
installs packages, contacts a service, or loads test files from the checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

BOUNDARIES = ("before_dispatch", "after_effect", "after_checkpoint")
EXPECTED_DEMO = {
    **{f"correct.{boundary}.kill": "pass" for boundary in BOUNDARIES},
    **{f"correct.{boundary}.noop": "inconclusive" for boundary in BOUNDARIES},
    **{f"uninstrumented.{boundary}.kill": "inconclusive" for boundary in BOUNDARIES},
    "duplicate.after_effect.kill": "fail",
    "forget_checkpoint.after_checkpoint.kill": "fail",
    "lose_task.before_dispatch.kill": "fail",
    "retry.before_dispatch.kill": "pass",
    "retry_forever.before_dispatch.kill": "fail",
}

INSTALL_PROBE = """
import importlib.metadata as metadata
import json
from pathlib import Path
import crashfixture

distribution = metadata.distribution('crashfixture')
origin = json.loads(distribution.read_text('direct_url.json') or '{}')
if origin.get('dir_info', {}).get('editable'):
    raise SystemExit('expected an installed wheel, found an editable install')
actual = Path(crashfixture.__file__).resolve()
expected = Path(distribution.locate_file('crashfixture/__init__.py')).resolve()
if actual != expected:
    raise SystemExit('imported source differs from the installed distribution')
entries = {(e.group, e.name): e.value for e in distribution.entry_points}
if entries.get(('console_scripts', 'crashfixture')) != 'crashfixture.cli:main':
    raise SystemExit('missing console entry point')
if entries.get(('pytest11', 'crashfixture')) != 'crashfixture.pytest_plugin':
    raise SystemExit('missing pytest entry point')
print(json.dumps({'version': distribution.version, 'package': str(actual),
                  'pytest': metadata.version('pytest')}))
"""

PYTEST_EXAMPLE = """
import sys
import pytest
from crashfixture import Boundary, Scenario

@pytest.mark.parametrize('boundary', list(Boundary))
def test_installed_fixture(recovery, boundary):
    result = recovery.run(
        [sys.executable, '-m', 'crashfixture.workers', 'correct'],
        Scenario(boundary),
    )
    result.require_pass()
    assert result.fault.witnessed
"""


def invoke(command: list[str], cwd: Path, env: dict[str, str], log: Path) -> None:
    with log.open("w", encoding="utf-8") as output:
        completed = subprocess.run(
            command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=30
        )
    if completed.returncode:
        raise RuntimeError(f"check exited {completed.returncode}; see {log}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", type=Path, required=True, help="interpreter with wheel and pytest"
    )
    parser.add_argument("--outdir", type=Path, default=Path("artifacts/onboarding"))
    args = parser.parse_args(argv)
    python = args.python.expanduser().absolute()  # Preserve the venv executable's symlink path.
    outdir = args.outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    receipt = {"status": "fail"}
    receipt_path = outdir / "receipt.json"
    receipt_path.write_text(
        '{"status": "fail", "error": "check did not complete"}\n', encoding="utf-8"
    )
    try:
        # Producers write only into a fresh directory; previous reports cannot
        # stand in for missing evidence from this invocation.
        directory = Path(tempfile.mkdtemp(prefix="run-", dir=outdir))
        receipt["evidence_dir"] = str(directory)
        with tempfile.TemporaryDirectory(prefix="crashfixture-onboarding-") as scratch:
            cwd = Path(scratch)
            env = {"PATH": os.defpath, "HOME": scratch, "TMPDIR": scratch}
            install_log = directory / "install.log"
            invoke([str(python), "-I", "-c", INSTALL_PROBE], cwd, env, install_log)
            receipt["installation"] = json.loads(install_log.read_text(encoding="utf-8"))
            console = python.parent / "crashfixture"
            invoke(
                [
                    str(console),
                    "demo",
                    "--workdir",
                    str(directory / "trials"),
                    "--json",
                    str(directory / "demo.json"),
                    "--junit",
                    str(directory / "demo.xml"),
                ],
                cwd,
                env,
                directory / "demo.log",
            )
            trials = json.loads((directory / "demo.json").read_text(encoding="utf-8"))["trials"]
            actual = {trial["name"]: trial["status"] for trial in trials}
            if len(trials) != len(EXPECTED_DEMO) or actual != EXPECTED_DEMO:
                raise RuntimeError("expected all 14 named control verdicts exactly once")
            counts = dict(Counter(trial["status"] for trial in trials))
            receipt["demo"] = counts
            example = cwd / "test_onboarding.py"
            example.write_text(PYTEST_EXAMPLE, encoding="utf-8")
            config = cwd / "pytest.ini"
            config.write_text("[pytest]\n", encoding="utf-8")
            invoke(
                [
                    str(python),
                    "-I",
                    "-m",
                    "pytest",
                    "-q",
                    "-c",
                    str(config),
                    f"--confcutdir={cwd}",
                    str(example),
                    f"--junitxml={directory / 'pytest.xml'}",
                ],
                cwd,
                env,
                directory / "pytest.log",
            )
            cases = list(ET.parse(directory / "pytest.xml").getroot().iter("testcase"))
            if len(cases) != 3 or any(
                case.find(tag) is not None
                for case in cases
                for tag in ("skipped", "failure", "error")
            ):
                raise RuntimeError("expected three passing fixture tests with no skipped cases")
            receipt["pytest_examples"] = 3
            for name in (
                "duplicate.after_effect.kill",
                "correct.after_effect.kill",
                "correct.after_effect.noop",
            ):
                trial = next(trial for trial in trials if trial["name"] == name)
                print(f"{name}: {trial['status']} {','.join(trial['reasons'])}".rstrip())
            print("onboarding: matched 14 controls; 3 installed-fixture tests passed")
            receipt["status"] = "pass"
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
        ET.ParseError,
    ) as exc:
        receipt["status"] = "fail"
        receipt["error"] = str(exc)
        print(f"onboarding failed: {exc}")
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

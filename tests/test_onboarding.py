"""Producer negative controls for the installed-wheel onboarding gate."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

import pytest

from crashfixture.cli import demo_cases
from scripts import check_onboarding


def demo_report():
    return {
        "trials": [
            {
                "name": f"{worker}.{scenario.boundary.value}.{fault}",
                "status": expected.value,
                "reasons": [],
            }
            for worker, scenario, fault, expected in demo_cases()
        ]
    }


def fixture_report(path, *, skipped=False):
    root = ET.Element("testsuite", tests="3")
    for number in range(3):
        case = ET.SubElement(root, "testcase", name=f"synthetic-{number}")
        if skipped and number == 0:
            ET.SubElement(case, "skipped")
    ET.ElementTree(root).write(path)


@pytest.mark.parametrize(
    "control",
    [
        "valid",
        "missing-demo",
        "missing-pytest",
        "swapped",
        "duplicate",
        "skipped",
        "missing-reasons",
    ],
)
def test_successful_producers_cannot_substitute_invalid_or_stale_evidence(
    tmp_path, monkeypatch, control
):
    outdir = tmp_path / "onboarding"
    outdir.mkdir()
    stale_demo = outdir / "demo.json"
    stale_demo.write_text(json.dumps(demo_report()))
    stale_pytest = outdir / "pytest.xml"
    fixture_report(stale_pytest)
    old = (stale_demo.read_bytes(), stale_pytest.read_bytes())
    (outdir / "receipt.json").write_text('{"status": "pass"}')

    def successful_producer(command, cwd, env, log):
        # All simulated producers return success. The gate must inspect fresh
        # evidence, not trust exit status or a prior run's documents.
        if log.name == "install.log":
            log.write_text(json.dumps({"version": "synthetic", "pytest": "synthetic"}))
        elif log.name == "demo.log" and control != "missing-demo":
            report = demo_report()
            if control == "swapped":
                for trial in report["trials"]:
                    if trial["name"] == "correct.after_effect.kill":
                        trial["status"] = "fail"
                    elif trial["name"] == "duplicate.after_effect.kill":
                        trial["status"] = "pass"
            elif control == "duplicate":
                report["trials"][1]["name"] = report["trials"][0]["name"]
            elif control == "missing-reasons":
                for trial in report["trials"]:
                    if trial["name"] == "duplicate.after_effect.kill":
                        del trial["reasons"]
            (log.parent / "demo.json").write_text(json.dumps(report))
        elif log.name == "pytest.log" and control != "missing-pytest":
            fixture_report(log.parent / "pytest.xml", skipped=control == "skipped")

    monkeypatch.setattr(check_onboarding, "invoke", successful_producer)
    code = check_onboarding.main(["--python", sys.executable, "--outdir", str(outdir)])
    assert code == (0 if control == "valid" else 1)
    receipt = json.loads((outdir / "receipt.json").read_text())
    assert receipt["status"] == ("pass" if control == "valid" else "fail")
    assert (stale_demo.read_bytes(), stale_pytest.read_bytes()) == old
    assert len(list(outdir.glob("run-*"))) == 1

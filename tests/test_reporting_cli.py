from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from crashfixture import Boundary, Harness, Scenario
from crashfixture.cli import main, worker_command
from crashfixture.reporting import write_json, write_junit


def test_json_and_junit_retain_failures_and_inconclusive_trials(recovery, tmp_path):
    trials = [
        ("good", recovery.run(worker_command("correct"), Scenario(Boundary.AFTER_EFFECT))),
        ("broken", recovery.run(worker_command("duplicate"), Scenario(Boundary.AFTER_EFFECT))),
        (
            "no-op",
            recovery.run(
                worker_command("correct"), Scenario(Boundary.AFTER_EFFECT), fault_mode="noop"
            ),
        ),
    ]
    json_path, xml_path = tmp_path / "report.json", tmp_path / "report.xml"
    write_json(json_path, trials)
    write_junit(xml_path, trials)
    report = json.loads(json_path.read_text())
    assert report["schema_version"] == 1
    assert [t["status"] for t in report["trials"]] == ["pass", "fail", "inconclusive"]
    assert report["trials"][0]["fault"]["wait_returncode"] == -9
    assert "score" not in json_path.read_text()
    suite = ET.parse(xml_path).getroot()
    assert suite.attrib == {
        "name": "crashfixture",
        "tests": "3",
        "failures": "1",
        "errors": "1",
        "skipped": "0",
    }
    assert len(suite.findall("testcase")) == 3
    assert len(suite.findall("testcase/failure")) == 1
    assert len(suite.findall("testcase/error")) == 1
    assert not suite.findall("testcase/skipped")


@pytest.mark.parametrize(
    ("mode", "fault_mode", "expected_exit", "status"),
    [
        ("correct", "kill", 0, "pass"),
        ("duplicate", "kill", 1, "fail"),
        ("correct", "noop", 2, "inconclusive"),
    ],
)
def test_cli_exit_contract(tmp_path, mode, fault_mode, expected_exit, status):
    report = tmp_path / "trial.json"
    code = main(
        [
            "run",
            "--worker",
            mode,
            "--boundary",
            "after_effect",
            "--fault-mode",
            fault_mode,
            "--workdir",
            str(tmp_path / "runs"),
            "--json",
            str(report),
        ]
    )
    assert code == expected_exit
    assert json.loads(report.read_text())["trials"][0]["status"] == status


def test_demo_checks_controls_and_emits_all_fourteen_trials(tmp_path):
    report = tmp_path / "demo.json"
    assert main(["demo", "--workdir", str(tmp_path / "runs"), "--json", str(report)]) == 0
    trials = json.loads(report.read_text())["trials"]
    assert len(trials) == 14
    assert [t["status"] for t in trials].count("pass") == 4
    assert [t["status"] for t in trials].count("fail") == 4
    assert [t["status"] for t in trials].count("inconclusive") == 6


def test_demo_rejects_an_unexpected_control_verdict(tmp_path, monkeypatch):
    original = Harness.run

    def suppress_fault(self, command, scenario, **kwargs):
        return original(self, command, scenario, fault_mode="noop")

    monkeypatch.setattr(Harness, "run", suppress_fault)
    assert main(["demo", "--workdir", str(tmp_path)]) == 1


def test_cli_invalid_budget_is_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["run", "--boundary", "before_dispatch", "--max-attempts", "99"])
    assert exc.value.code == 2

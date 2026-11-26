from __future__ import annotations

import json
import os
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


@pytest.mark.parametrize("action", ["run", "demo"])
@pytest.mark.parametrize("alias", ["same", "relative", "symlink", "hardlink"])
def test_cli_rejects_report_aliases_before_running(tmp_path, monkeypatch, capsys, action, alias):
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "report"
    other = tmp_path / "other"
    if alias == "relative":
        other = "report"
    elif alias == "same":
        other = report
    elif alias == "symlink":
        other.symlink_to(report)  # A dangling alias to a not-yet-written report.
    else:
        report.write_text("previous evidence", encoding="utf-8")
        os.link(report, other)

    args = [action, "--boundary", "after_effect"] if action == "run" else [action]
    workdir = tmp_path / "runs"
    with pytest.raises(SystemExit) as exc:
        main([*args, "--workdir", str(workdir), "--json", str(report), "--junit", str(other)])
    assert exc.value.code == 2
    assert "--json and --junit must use distinct output files" in capsys.readouterr().err
    assert not workdir.exists()
    if alias == "hardlink":
        assert report.read_text(encoding="utf-8") == "previous evidence"
        assert other.read_text(encoding="utf-8") == "previous evidence"
    else:
        assert not report.exists()


def test_cli_writes_both_report_formats_to_distinct_paths(tmp_path):
    json_path, xml_path = tmp_path / "report.json", tmp_path / "report.xml"
    assert (
        main(
            [
                "run",
                "--boundary",
                "after_effect",
                "--workdir",
                str(tmp_path / "runs"),
                "--json",
                str(json_path),
                "--junit",
                str(xml_path),
            ]
        )
        == 0
    )
    assert json.loads(json_path.read_text())["trials"][0]["status"] == "pass"
    assert ET.parse(xml_path).getroot().attrib["errors"] == "0"


def test_cli_case_only_destinations_respect_filesystem(tmp_path, capsys):
    probe = tmp_path / "probe"
    probe.touch()
    case_insensitive = (tmp_path / "Probe").exists()
    json_path, xml_path = tmp_path / "report", tmp_path / "Report"
    args = [
        "run",
        "--boundary",
        "after_effect",
        "--workdir",
        str(tmp_path / "runs"),
        "--json",
        str(json_path),
        "--junit",
        str(xml_path),
    ]
    if case_insensitive:
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 2
        assert "must use distinct output files" in capsys.readouterr().err
        assert json_path.samefile(xml_path)
    else:
        assert main(args) == 0
        assert ET.parse(xml_path).getroot().attrib["errors"] == "0"
    assert json.loads(json_path.read_text())["trials"][0]["status"] == "pass"

"""Opt-in checks for the executable adoption demo, using its actual workers."""

import json

import demo


def test_demo_preserves_both_gates_and_expected_controls(tmp_path):
    assert demo.main(["--output", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["status"] == "complete"
    assert report["calibration_passed"] is True
    assert len(report["cases"]) == 4
    cases = {case["name"]: case for case in report["cases"]}
    assert cases["wrong_thread"]["core"]["status"] == "pass"
    assert cases["wrong_thread"]["saver"]["status"] == "fail"
    assert cases["noop"]["saver"]["status"] == "inconclusive"
    assert sum(case["core"]["fault"]["sent_signal"] == 9 for case in report["cases"]) == 3
    assert all(case["expectations_met"] for case in report["cases"])


def test_disabled_injection_cannot_calibrate(tmp_path, monkeypatch):
    original = demo.Harness.run

    def noop(self, command, scenario, **kwargs):
        return original(self, command, scenario, **{**kwargs, "fault_mode": "noop"})

    monkeypatch.setattr(demo.Harness, "run", noop)
    assert demo.main(["--output", str(tmp_path)]) == 1
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["calibration_passed"] is False
    assert [case["expectations_met"] for case in report["cases"]] == [False, False, False, True]


def test_preflight_failure_replaces_stale_success(tmp_path, monkeypatch):
    (tmp_path / "latest.json").write_text('{"calibration_passed": true}')
    monkeypatch.setattr(demo, "version", lambda package: "0.0.0")
    assert demo.main(["--output", str(tmp_path)]) == 2
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["status"] == "error"
    assert report["calibration_passed"] is False
    assert report["cases"] == []
    assert json.loads((tmp_path / report["run_dir"] / "demo-result.json").read_text()) == report

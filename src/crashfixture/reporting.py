"""Minimal evidence JSON and JUnit, retaining every trial including invalid ones."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from .model import Result, Status


def write_json(path: str | Path, trials: Sequence[tuple[str, Result]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(
            {"schema_version": 1, "trials": [{"name": name, **r.to_dict()} for name, r in trials]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def write_junit(path: str | Path, trials: Sequence[tuple[str, Result]]) -> None:
    suite = ET.Element(
        "testsuite",
        name="crashfixture",
        tests=str(len(trials)),
        failures=str(sum(r.status is Status.FAIL for _, r in trials)),
        errors=str(sum(r.status is Status.INCONCLUSIVE for _, r in trials)),
        skipped="0",
    )
    for name, result in trials:
        case = ET.SubElement(suite, "testcase", name=name, classname="crashfixture")
        properties = ET.SubElement(case, "properties")
        ET.SubElement(properties, "property", name="status", value=result.status.value)
        if result.status is not Status.PASS:
            tag = "error" if result.status is Status.INCONCLUSIVE else "failure"
            ET.SubElement(
                case,
                tag,
                type=f"crashfixture.{result.status.value}",
                message=", ".join(result.reasons),
            )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)

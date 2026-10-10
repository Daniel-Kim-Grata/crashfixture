"""Require a tag matching source version; prepare artifacts, never publish."""

import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def main() -> int:
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    version = tomllib.loads(project.read_text())["project"]["version"]
    if len(sys.argv) != 2 or not re.fullmatch(r"v\d+\.\d+\.\d+", sys.argv[1]):
        print("expected a release tag such as v0.1.0", file=sys.stderr)
        return 1
    if sys.argv[1] != "v" + version:
        print("tag does not match pyproject.toml version", file=sys.stderr)
        return 1
    print(f"release source version: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

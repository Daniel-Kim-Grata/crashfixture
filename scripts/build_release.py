"""Build with setuptools, then normalize sdist metadata using SOURCE_DATE_EPOCH."""

from __future__ import annotations

import argparse
import copy
import gzip
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def source_epoch() -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        try:
            raw = subprocess.check_output(
                ["git", "log", "-1", "--format=%ct"], cwd=ROOT, text=True, timeout=5
            ).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("set SOURCE_DATE_EPOCH when building outside a Git checkout") from exc
    if not re.fullmatch(r"[0-9]+", raw) or not 0 <= int(raw) <= 0xFFFFFFFF:
        raise ValueError("SOURCE_DATE_EPOCH must be an unsigned 32-bit integer")
    return int(raw)


def normalize_sdist(path: Path, epoch: int) -> None:
    """Preserve regular file bytes, names, and executable intent; extract nothing.

    This is a release helper for our own setuptools archives, not an arbitrary
    archive sanitizer. Invalid members leave the original archive untouched.
    """
    if type(epoch) is not int or not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("epoch must be an unsigned 32-bit integer")
    temporary = None
    try:
        with tarfile.open(path, "r:gz") as source:
            members = sorted(source.getmembers(), key=lambda member: member.name)
            # Tar EOF precedes gzip EOF. Drain bounded chunks so CRC/trailer
            # failures cannot be hidden by rewriting an otherwise readable tar.
            while source.fileobj.read(64 * 1024):
                pass
            names = [member.name for member in members]
            if len(set(names)) != len(names):
                raise ValueError("duplicate sdist member")
            for member in members:
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts:
                    raise ValueError("invalid sdist member path")
                if (
                    member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
                    or member.sparse is not None
                ):
                    raise ValueError("sdist members must be regular files or directories")
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as raw:
                temporary = Path(raw.name)
                with (
                    gzip.GzipFile(
                        fileobj=raw, mode="wb", filename="", mtime=epoch, compresslevel=9
                    ) as zipped,
                    tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as target,
                ):
                    for original in members:
                        member = copy.copy(original)
                        member.mtime = epoch
                        member.uid = member.gid = 0
                        member.uname = member.gname = ""
                        member.mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
                        member.pax_headers = {}  # tarfile regenerates any required path headers.
                        if member.isfile():
                            with source.extractfile(original) as payload:
                                target.addfile(member, payload)
                        else:
                            target.addfile(member)
                raw.flush()
                os.fchmod(raw.fileno(), 0o644)
                os.fsync(raw.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    try:
        epoch = source_epoch()
    except ValueError as exc:
        parser.error(str(exc))
    directory = args.outdir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Stage a fresh pair, so stale artifacts cannot be normalized accidentally.
    with tempfile.TemporaryDirectory(prefix="crashfixture-build-", dir=directory.parent) as stage:
        subprocess.run(
            [sys.executable, "-m", "build", "--no-isolation", "--outdir", stage],
            cwd=ROOT,
            env={**os.environ, "SOURCE_DATE_EPOCH": str(epoch)},
            check=True,
            timeout=120,
        )
        sdists = list(Path(stage).glob("*.tar.gz"))
        wheels = list(Path(stage).glob("*.whl"))
        if len(sdists) != 1 or len(wheels) != 1:
            raise ValueError("expected exactly one sdist and one wheel")
        normalize_sdist(sdists[0], epoch)
        for artifact in [sdists[0], wheels[0]]:
            artifact.replace(directory / artifact.name)
    print(f"release metadata normalized at SOURCE_DATE_EPOCH={epoch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import gzip
import io
import struct
import tarfile
from pathlib import Path

import pytest

from scripts.build_release import normalize_sdist, source_epoch


def archive(path: Path, stamp: int, *, changed: bool = False, reverse: bool = False) -> None:
    members = [
        ("package", None, 0o700),
        ("package/source.py", b"changed" if changed else b"synthetic-source", 0o600),
        ("package/run", b"synthetic-executable", 0o700),
        ("package/" + "long-name-" * 15, b"long-name-payload", 0o600),
    ]
    with gzip.GzipFile(filename=str(path), mode="wb", mtime=stamp) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as target:
            for name, payload, mode in reversed(members) if reverse else members:
                member = tarfile.TarInfo(name)
                member.mtime = stamp + 0.125
                member.uid, member.gid = stamp, stamp + 1
                member.uname, member.gname = f"builder-{stamp}", f"group-{stamp}"
                member.mode = mode
                member.pax_headers = {"atime": str(stamp), "ctime": str(stamp)}
                if payload is None:
                    member.type = tarfile.DIRTYPE
                    target.addfile(member)
                else:
                    member.size = len(payload)
                    target.addfile(member, io.BytesIO(payload))


def payloads(path: Path) -> dict[str, bytes]:
    with tarfile.open(path) as source:
        return {m.name: source.extractfile(m).read() for m in source.getmembers() if m.isfile()}


def test_metadata_and_order_variance_normalize_without_changing_payloads(tmp_path):
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    archive(first, 100)
    archive(second, 200, reverse=True)
    expected = payloads(first)
    assert first.read_bytes() != second.read_bytes()
    normalize_sdist(first, 300)
    normalize_sdist(second, 300)
    assert first.stat().st_mode & 0o777 == 0o644
    assert first.read_bytes() == second.read_bytes()
    assert payloads(first) == payloads(second) == expected
    header = first.read_bytes()[:10]
    assert struct.unpack("<I", header[4:8])[0] == 300
    assert not header[3] & 8  # No variable gzip filename field.
    with tarfile.open(first) as source:
        members = source.getmembers()
        assert [m.name for m in members] == sorted(m.name for m in members)
        for member in members:
            assert member.mtime == 300
            assert member.uid == member.gid == 0
            assert member.uname == member.gname == ""
            assert not {"mtime", "atime", "ctime"}.intersection(member.pax_headers)
            assert member.mode == (
                0o755 if member.isdir() or member.name == "package/run" else 0o644
            )
    original = first.read_bytes()
    normalize_sdist(first, 300)
    assert first.read_bytes() == original  # Applying normalization twice is stable.


def test_normalization_retains_content_and_epoch_differences(tmp_path):
    first, changed, later = [tmp_path / f"{name}.tar.gz" for name in ("first", "changed", "later")]
    archive(first, 100)
    archive(changed, 100, changed=True)
    archive(later, 100)
    for path, epoch in ((first, 300), (changed, 300), (later, 301)):
        normalize_sdist(path, epoch)
    assert first.read_bytes() != changed.read_bytes()
    assert first.read_bytes() != later.read_bytes()
    assert payloads(first) == payloads(later)
    assert payloads(first) != payloads(changed)


@pytest.mark.parametrize("kind", ["traversal", "absolute", "symlink", "duplicate"])
def test_invalid_archive_is_rejected_without_replacing_original(tmp_path, kind):
    path = tmp_path / "invalid.tar.gz"
    with tarfile.open(path, "w:gz") as target:
        names = {
            "traversal": ["package/../outside"],
            "absolute": ["/outside"],
            "symlink": ["package/link"],
            "duplicate": ["package/same", "package/same"],
        }[kind]
        for name in names:
            member = tarfile.TarInfo(name)
            if kind == "symlink":
                member.type, member.linkname = tarfile.SYMTYPE, "outside"
            target.addfile(member)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        normalize_sdist(path, 300)
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


@pytest.mark.parametrize("damage", ["crc", "missing-trailer"])
def test_gzip_integrity_failure_leaves_original_untouched(tmp_path, damage):
    path = tmp_path / "corrupted.tar.gz"
    archive(path, 100)
    content = bytearray(path.read_bytes())
    if damage == "crc":
        content[-8] ^= 1
    else:
        del content[-8:]
    path.write_bytes(content)
    original = path.read_bytes()
    with pytest.raises((gzip.BadGzipFile, EOFError)):
        normalize_sdist(path, 300)
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


def test_sparse_file_is_rejected_without_losing_logical_content(tmp_path):
    path = tmp_path / "sparse.tar.gz"
    member = tarfile.TarInfo("package/sparse")
    member.type = tarfile.GNUTYPE_SPARSE
    member.size = 3
    header = bytearray(member.tobuf(format=tarfile.GNU_FORMAT))

    def number(value):
        return (f"{value:011o}" + "\0").encode("ascii")

    header[386:398] = number(0)  # First sparse extent offset.
    header[398:410] = number(3)  # Three stored bytes.
    header[483:495] = number(10)  # Ten logical bytes, including seven trailing zeros.
    header[148:156] = b" " * 8
    header[148:156] = (f"{sum(header):06o}" + "\0 ").encode("ascii")
    path.write_bytes(gzip.compress(bytes(header) + b"abc" + bytes(509) + bytes(1024)))
    assert payloads(path) == {"package/sparse": b"abc" + bytes(7)}
    original = path.read_bytes()
    with pytest.raises(ValueError, match="regular files or directories"):
        normalize_sdist(path, 300)
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


@pytest.mark.parametrize("raw", ["", "-1", "1.5", "4294967296", "not-a-date"])
def test_invalid_environment_epoch_fails_explicitly(monkeypatch, raw):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", raw)
    with pytest.raises(ValueError, match="unsigned 32-bit"):
        source_epoch()


def test_explicit_epoch_is_used_without_git(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1791590400")
    assert source_epoch() == 1791590400

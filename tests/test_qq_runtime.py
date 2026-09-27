from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jev_open.context.providers.qq_runtime import (
    QQRuntimeError,
    fingerprint_allowed,
    read_qq_version_fingerprint,
)


def test_qq_runtime_fingerprint_is_read_only_and_hashes_only_qq_exe(tmp_path: Path):
    executable = tmp_path / "QQ.exe"
    executable.write_bytes(b"synthetic QQ executable fixture")
    (tmp_path / "chat.db").write_text("must never be opened", encoding="utf-8")

    result = read_qq_version_fingerprint(executable)

    assert result.path == executable.resolve()
    assert result.file_size == executable.stat().st_size
    assert result.file_sha256 == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert result.version_key in {result.file_sha256, result.file_version, result.product_version}


@pytest.mark.parametrize(
    "name, contents",
    [("qq.dll", b"dll"), ("QQ.exe", b""), ("QQ.exe", None)],
)
def test_qq_runtime_rejects_non_executable_or_empty_files(
    tmp_path: Path, name: str, contents: bytes | None
):
    path = tmp_path / name
    if contents is None:
        path.mkdir()
    else:
        path.write_bytes(contents)
    with pytest.raises(QQRuntimeError):
        read_qq_version_fingerprint(path)


def test_qq_runtime_allow_list_requires_explicit_match(tmp_path: Path):
    executable = tmp_path / "QQ.exe"
    executable.write_bytes(b"synthetic")
    fingerprint = read_qq_version_fingerprint(executable)
    assert fingerprint_allowed(fingerprint, sha256=frozenset({fingerprint.file_sha256}))
    assert not fingerprint_allowed(fingerprint, sha256=frozenset({"0" * 64}))
    assert not fingerprint_allowed(fingerprint)

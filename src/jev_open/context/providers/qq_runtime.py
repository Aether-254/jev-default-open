"""Read-only QQ executable identity checks.

This module deliberately has no QQ process, renderer, database, or chat access.
It is a small boundary used by a future version-specific adapter to prove that
the adapter is attached to the expected ``QQ.exe`` build before it is allowed
to resolve a click.  The file digest is the stable identity; Windows version
resources are optional metadata and are never treated as authentication.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path


class QQRuntimeError(ValueError):
    """Raised when a candidate executable cannot be safely fingerprinted."""


@dataclass(frozen=True, slots=True)
class QQVersionFingerprint:
    """A bounded, read-only identity for one QQ executable file."""

    path: Path
    file_sha256: str
    file_size: int
    file_version: str | None = None
    product_version: str | None = None
    company_name: str | None = None
    product_name: str | None = None

    @property
    def version_key(self) -> str:
        """Return a stable key suitable for an explicit adapter allow-list."""
        return self.file_version or self.product_version or self.file_sha256


def _validate_qq_path(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.name.casefold() != "qq.exe":
        raise QQRuntimeError("QQ runtime path must be an absolute QQ.exe file")
    try:
        if candidate.is_symlink() or not candidate.is_file():
            raise QQRuntimeError("QQ runtime path must be a regular non-link file")
        resolved = candidate.resolve(strict=True)
        stat = resolved.stat()
    except OSError as exc:
        raise QQRuntimeError("QQ runtime path is not readable") from exc
    if stat.st_size <= 0 or stat.st_size > 512 * 1024 * 1024:
        raise QQRuntimeError("QQ runtime file size is outside the bounded range")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise QQRuntimeError("QQ runtime file cannot be hashed") from exc
    return digest.hexdigest()


def _version_strings(path: Path) -> dict[str, str]:
    """Read VERSIONINFO strings through version.dll, when running on Windows."""
    if os.name != "nt":
        return {}
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        size = version.GetFileVersionInfoSizeW(ctypes.c_wchar_p(str(path)), None)
        if not size:
            return {}
        blob = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(ctypes.c_wchar_p(str(path)), 0, size, blob):
            return {}

        def query(key: str) -> tuple[int, int, int, int] | str | None:
            pointer = ctypes.c_void_p()
            length = ctypes.c_uint()
            if not version.VerQueryValueW(
                blob, ctypes.c_wchar_p(key), ctypes.byref(pointer), ctypes.byref(length)
            ):
                return None
            if key == "\\":
                if length.value < 8:
                    return None
                data = ctypes.string_at(pointer, length.value)
                # VS_FIXEDFILEINFO starts with the signature, then four 32-bit
                # values.  The version fields occupy dwFileVersionMS/LS.
                values = struct.unpack_from("<IIIII", data, 0)
                if values[0] != 0xFEEF04BD:
                    return None
                return values[2] >> 16, values[2] & 0xFFFF, values[3] >> 16, values[3] & 0xFFFF
            if length.value == 0:
                return None
            return ctypes.wstring_at(pointer, length.value).rstrip("\x00")

        fixed = query("\\")
        result: dict[str, str] = {}
        if isinstance(fixed, tuple):
            result["file_version"] = ".".join(str(part) for part in fixed)
            result["product_version"] = result["file_version"]

        languages: list[tuple[int, int]] = []
        # The translation value is an array of LANGID/code-page pairs.  It is
        # binary data, so it must not go through the string query helper.
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if version.VerQueryValueW(
            blob,
            ctypes.c_wchar_p("\\VarFileInfo\\Translation"),
            ctypes.byref(pointer),
            ctypes.byref(length),
        ):
            raw = ctypes.string_at(pointer, length.value)
            languages = [
                struct.unpack_from("<HH", raw, offset)
                for offset in range(0, len(raw) - 3, 4)
            ]
        languages = languages or [(0x0409, 1200)]
        for language, code_page in languages:
            prefix = f"\\StringFileInfo\\{language:04x}{code_page:04x}\\"
            for field in ("FileVersion", "ProductVersion", "CompanyName", "ProductName"):
                value = query(prefix + field)
                if isinstance(value, str) and value:
                    result.setdefault(
                        {
                            "FileVersion": "file_version",
                            "ProductVersion": "product_version",
                            "CompanyName": "company_name",
                            "ProductName": "product_name",
                        }[field],
                        value,
                    )
            if len(result) >= 4:
                break
        return result
    except (OSError, AttributeError, ctypes.ArgumentError, ValueError, struct.error):
        return {}


def read_qq_version_fingerprint(path: Path) -> QQVersionFingerprint:
    """Return QQ.exe metadata and a SHA-256 identity without reading chat data."""
    resolved = _validate_qq_path(path)
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise QQRuntimeError("QQ runtime file metadata is unavailable") from exc
    metadata = _version_strings(resolved)
    return QQVersionFingerprint(
        path=resolved,
        file_sha256=_sha256(resolved),
        file_size=size,
        file_version=metadata.get("file_version"),
        product_version=metadata.get("product_version"),
        company_name=metadata.get("company_name"),
        product_name=metadata.get("product_name"),
    )


def fingerprint_allowed(
    fingerprint: QQVersionFingerprint,
    *,
    sha256: frozenset[str] = frozenset(),
    versions: frozenset[str] = frozenset(),
) -> bool:
    """Match only an explicit digest or version allow-list supplied by config."""
    if sha256 and fingerprint.file_sha256.casefold() in {item.casefold() for item in sha256}:
        return True
    return bool(versions) and any(
        value == fingerprint.file_version or value == fingerprint.product_version
        for value in versions
    )

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jev_open.domain import FileTarget

from .preferences import canonical_windows_path

_CHUNK_SIZE = 65536
_NO_CONTENT_READ = 0x400 | 0x1000 | 0x40000 | 0x400000


def _handle_metadata(source) -> Any:
    if os.name != "nt":
        return os.fstat(source.fileno())
    import msvcrt
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME),
            ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    get_info = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    get_info.restype = wintypes.BOOL
    info = FileInformation()
    if not get_info(msvcrt.get_osfhandle(source.fileno()), ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return SimpleNamespace(
        st_dev=info.volume,
        st_ino=(info.index_high << 32) | info.index_low,
        st_size=(info.size_high << 32) | info.size_low,
        st_mtime_ns=(
            ((info.written.dwHighDateTime << 32) | info.written.dwLowDateTime) - 116444736000000000
        )
        * 100,
        st_file_attributes=info.attributes,
    )


@dataclass(frozen=True)
class FileFingerprint:
    path: str
    size: int
    modified_ns: int
    head_sha256: str
    tail_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def matches(self, other: dict[str, Any]) -> bool:
        return all(
            other.get(field) == getattr(self, field)
            for field in ("size", "modified_ns", "head_sha256", "tail_sha256")
        )


def _local_path_location(path: Path) -> bool:
    value = str(path)
    if not path.is_absolute() or value.startswith(("\\\\", "//")):
        return False
    if os.name == "nt":
        if ":" in value[2:]:
            return False
        get_drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint32
        if get_drive_type(path.anchor) not in (3, 6):
            return False
    return True


def _local_plain_path(path: Path) -> bool:
    if not _local_path_location(path):
        return False
    try:
        # Inspect ancestors before descendants so junctions cannot redirect a later metadata read.
        for element in (*reversed(path.parents), path):
            info = element.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & _NO_CONTENT_READ
            ):
                return False
    except OSError:
        return False
    return True


def fingerprint_file(target: FileTarget) -> FileFingerprint | None:
    """Read at most 128KiB from this explicit local file; never traverse or hydrate placeholders."""
    path = target.path
    if not _local_plain_path(path):
        return None
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            return None
        if target.size is not None and target.size != before.st_size:
            return None
        if (
            target.modified_at is not None
            and abs(target.modified_at.timestamp() - before.st_mtime) > 0.000001
        ):
            return None
        with path.open("rb") as source:
            opened = _handle_metadata(source)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return None
            if getattr(opened, "st_file_attributes", 0) & _NO_CONTENT_READ:
                return None
            head = source.read(_CHUNK_SIZE)
            source.seek(max(0, opened.st_size - _CHUNK_SIZE))
            tail = source.read(_CHUNK_SIZE)
            after = _handle_metadata(source)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        return FileFingerprint(
            path=canonical_windows_path(str(path)),
            size=before.st_size,
            modified_ns=before.st_mtime_ns,
            head_sha256=hashlib.sha256(head).hexdigest(),
            tail_sha256=hashlib.sha256(tail).hexdigest(),
        )
    except OSError:
        return None


def known_path_exists(path: str) -> bool | None:
    """Metadata-only cleanup of an indexed path; unsafe paths remain untouched."""
    candidate = Path(path)
    if not _local_path_location(candidate):
        return None
    try:
        for element in (*reversed(candidate.parents), candidate):
            metadata = element.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & _NO_CONTENT_READ
            ):
                return None
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None

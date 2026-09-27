from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from jev_open.domain import FileTarget, OpenRequest, OpenVerb


def test_file_target_and_request_are_valid_domain_values() -> None:
    target = FileTarget(
        path=Path("C:/demo/report.xlsx"),
        extension=".xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size=128,
    )
    request = OpenRequest(
        request_id="request-1",
        source_pid=42,
        source_executable=Path("C:/Windows/explorer.exe"),
        verb=OpenVerb.OPEN,
        target=str(target.path),
        captured_at=datetime.now(UTC),
    )

    assert target.kind == "file"
    assert request.verb is OpenVerb.OPEN


def test_domain_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        FileTarget(path=Path("C:/bad.bin"), extension=".bin", size=-1)
    with pytest.raises(ValidationError):
        OpenRequest(
            request_id="bad",
            source_pid=0,
            source_executable=Path("C:/bad.exe"),
            verb=OpenVerb.OPEN,
            target="C:/bad.bin",
            captured_at=datetime.now(UTC),
        )

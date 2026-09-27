from datetime import UTC, datetime
from pathlib import Path

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
    # TODO: Add negative size, PID, confidence, and invalid discriminated-union cases.
    pass

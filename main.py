import sys
from pathlib import Path

# Support the documented source-checkout launcher without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jev_open.app import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())

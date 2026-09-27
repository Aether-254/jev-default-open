"""Exercise native host pipe shutdown without loading any global hook."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


def run_case(executable: Path, case: str) -> None:
    process = subprocess.Popen(
        [str(executable), "--test-mode", "--nonce", "synthetic-transport-test-1234"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        if case == "stdin_eof":
            process.stdin.close()
        elif case == "stdout_backpressure":
            # The echoed command exceeds the unread anonymous pipe buffer.
            command = json.dumps({"command_id": "large", "command": "x" * 60000})
            process.stdin.write(command.encode() + b"\n")
            process.stdin.flush()
            time.sleep(0.1)
            process.stdin.write(b'{"command_id":"exit","command":"exit"}\n')
            process.stdin.flush()
        else:
            process.stdin.write(b'{"command_id":"exit","command":"exit"}\n')
            process.stdin.flush()
        process.wait(timeout=5)
        assert process.returncode == 0, (case, process.returncode)
        if case == "exit_with_stdin_open":
            replies = [json.loads(line) for line in process.stdout.read().splitlines()]
            assert replies[0]["test_mode"] is True
            assert any(reply.get("command") == "exit" for reply in replies)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def main() -> None:
    executable = Path(sys.argv[1]).resolve(strict=True)
    result = subprocess.run(
        [str(executable), "--self-test"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    status = json.loads(result.stdout)
    assert status["protocol_version"] == 2
    assert status["test_mode_supported"] is True
    for case in ("exit_with_stdin_open", "stdin_eof", "stdout_backpressure"):
        run_case(executable, case)
        print(f"{case}: passed")


if __name__ == "__main__":
    main()

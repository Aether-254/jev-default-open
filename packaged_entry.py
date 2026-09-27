from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

# Keep source-checkout execution identical to main.py.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def _embedded_key() -> str | None:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    key_file = bundle_root / "jev_api_key.txt"
    try:
        value = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _hook_smoke() -> int:
    import asyncio

    from jev_open.interception.native import NativeInterceptionModule

    async def run() -> int:
        root = _bundle_root()
        host = root / "native_host.exe"
        probe = root / "hook_capture_probe.exe"
        captured: list[str] = []
        event = asyncio.Event()

        async def handler(request) -> None:
            captured.append(request.target)
            event.set()

        module = NativeInterceptionModule(host)
        try:
            await module.start(handler)
            await asyncio.sleep(0.25)
            for arguments in ((), ("--ex-noasync",)):
                event.clear()
                process = await asyncio.create_subprocess_exec(
                    str(probe),
                    *arguments,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(process.communicate(), 5)
                await asyncio.wait_for(event.wait(), 2)
                print(stdout.decode(errors="replace").strip())
            print(f"HOOK_SMOKE_OK captured={len(captured)}")
            return 0 if len(captured) == 2 else 1
        finally:
            await module.stop()

    try:
        return asyncio.run(run())
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"HOOK_SMOKE_FAILED {type(exc).__name__}", file=sys.stderr)
        return 1


def main() -> int:
    if "--qt-smoke" in sys.argv:
        from PySide6.QtCore import qVersion

        print(f"QT_SMOKE_OK {qVersion()}")
        return 0
    if "--hook-smoke" in sys.argv:
        return _hook_smoke()
    if "--open-target" in sys.argv:
        index = sys.argv.index("--open-target")
        if index + 1 < len(sys.argv):
            from jev_open.single_instance import forward_open_target

            if forward_open_target(sys.argv[index + 1]):
                print("Forwarded target to the running Jev instance.")
                return 0
    no_key_commands = {
        "--demo",
        "--qt-smoke",
        "--hook-smoke",
        "--register-explorer",
        "--unregister-explorer",
        "--self-test",
        "--match",
    }
    needs_key = not any(command in sys.argv for command in no_key_commands)
    if needs_key and not os.environ.get("TYPESAFE_API_KEY"):
        embedded = _embedded_key()
        if embedded:
            os.environ["TYPESAFE_API_KEY"] = embedded
    if needs_key and not os.environ.get("TYPESAFE_API_KEY"):
        print("TypeSafe API key is required for live mode.")
        print("The key is kept in this process only and is not written to disk.")
        try:
            key = getpass.getpass("TYPESAFE_API_KEY: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 130
        if not key:
            print("No API key supplied.")
            return 2
        os.environ["TYPESAFE_API_KEY"] = key

    from jev_open.app import run

    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import load_config


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jev Default Open")
    parser.add_argument("--config", type=Path, help="Explicit local configuration file")
    parser.add_argument("--self-test", action="store_true", help="Read-only diagnostics, no hooks")
    parser.add_argument(
        "--demo", action="store_true", help="Synthetic offline UI, no hooks or launches"
    )
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.self_test:
            from .diagnostics import run_self_tests

            report = asyncio.run(run_self_tests(config))
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 0 if report["ok"] else 1
        if args.demo:
            from .demo import run_demo

            return run_demo(config)
        from .bootstrap import build_application

        application = build_application(config)
        return application.run()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Exceptions may contain private targets, tokens, or HTTP URLs.
        print(
            f"Startup failed ({type(exc).__name__}); run --self-test for diagnostics.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(run())

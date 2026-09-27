from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .config import load_config


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jev Default Open")
    parser.add_argument("--config", type=Path, help="Explicit local configuration file")
    parser.add_argument("--self-test", action="store_true", help="Read-only diagnostics, no hooks")
    parser.add_argument(
        "--demo", action="store_true", help="Synthetic offline UI, no hooks or launches"
    )
    parser.add_argument(
        "--match", metavar="TARGET", help="List matching installed apps for a path or URL"
    )
    parser.add_argument("--open-target", metavar="TARGET", help="Handle a target from Explorer")
    parser.add_argument("--register-explorer", action="store_true")
    parser.add_argument("--unregister-explorer", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.self_test:
            from .diagnostics import run_self_tests

            report = asyncio.run(run_self_tests(config))
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 0 if report["ok"] else 1
        if args.register_explorer or args.unregister_explorer:
            from .explorer import register_explorer, unregister_explorer

            result = register_explorer() if args.register_explorer else unregister_explorer()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.demo:
            from .demo import run_demo

            return run_demo(config)
        if args.match is not None:
            from .actions.windows import WindowsOpenActionModule
            from .domain import FileTarget, UrlTarget

            target_text = args.match
            parsed = urlsplit(target_text)
            if parsed.scheme and parsed.netloc:
                target = UrlTarget(
                    url=target_text,
                    scheme=parsed.scheme.casefold(),
                    host=parsed.hostname.casefold() if parsed.hostname else None,
                )
            else:
                path = Path(target_text).expanduser()
                extension = path.suffix.casefold()
                target = FileTarget(
                    path=path,
                    extension=extension,
                    mime_type=mimetypes.guess_type(path.name)[0],
                )
            actions = asyncio.run(
                WindowsOpenActionModule(
                    config.configured_actions,
                    profile_labels=config.profile_labels,
                ).discover(target)
            )
            print(
                json.dumps(
                    {
                        "target": target_text,
                        "matches": [
                            {
                                "action_id": action.action_id,
                                "application_id": action.application_id,
                                "display_name": action.display_name,
                                "profile_id": action.profile_id,
                                "invocation_kind": action.invocation_kind,
                                "is_default": action.invocation_data.get("is_default", False),
                            }
                            for action in actions
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        from .bootstrap import build_application
        from .domain import OpenRequest, OpenVerb

        application = build_application(config)
        if args.open_target is not None:
            application.initial_requests = (
                OpenRequest(
                    request_id=uuid4().hex,
                    source_pid=os.getpid(),
                    source_executable=Path(sys.executable).resolve(),
                    verb=OpenVerb.OPEN,
                    target=args.open_target,
                    captured_at=datetime.now(UTC),
                ),
            )
        return application.run()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Exceptions may contain private targets, tokens, or HTTP URLs.
        if os.environ.get("JEV_DEBUG_STARTUP") == "1":
            traceback.print_exc()
        print(
            f"Startup failed ({type(exc).__name__}); run --self-test for diagnostics.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(run())

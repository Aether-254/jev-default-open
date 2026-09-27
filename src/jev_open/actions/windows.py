from __future__ import annotations

from jev_open.domain import ContextEnvelope, OpenAction, OpenTarget


class WindowsOpenActionModule:
    async def discover(self, target: OpenTarget) -> tuple[OpenAction, ...]:
        # TODO: Enumerate file handlers through SHAssocEnumHandlers.
        # TODO: Enumerate protocol handlers through RegisteredApplications.
        # TODO: Expand discovered browser and VS Code profiles into Open Actions.
        # TODO: Include configured actions and remove stale or incompatible entries.
        # TODO: Preserve default and preference actions before the 255-item cap.
        raise NotImplementedError("TODO: discover Windows Open Actions")

    async def launch(self, action: OpenAction, context: ContextEnvelope) -> None:
        # TODO: Revalidate the executable/profile immediately before launch.
        # TODO: Prefer IAssocHandler invocation where available.
        # TODO: Launch argv arrays without cmd.exe or string concatenation.
        # TODO: Surface failure to the confirmation UI without automatic fallback.
        raise NotImplementedError("TODO: launch selected Open Action")

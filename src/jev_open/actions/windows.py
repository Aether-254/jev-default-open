from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import ntpath
import os
import re
import shutil
import subprocess
from ctypes import wintypes
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None  # type: ignore[assignment]

from jev_open.domain import ContextEnvelope, FileTarget, OpenAction, OpenTarget, UrlTarget
from jev_open.domain.errors import LaunchFailed
from jev_open.process_env import sanitized_child_environment

_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".xml",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".log",
        ".py",
        ".pyi",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".ts",
        ".tsx",
        ".jsx",
        ".css",
        ".html",
        ".sql",
        ".ipynb",
        ".tex",
        ".r",
        ".jl",
        ".vue",
        ".svelte",
        ".gitignore",
        ".dockerfile",
        ".sh",
    }
)
_SCRIPT_HOSTS = frozenset(
    {
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "wscript.exe",
        "cscript.exe",
        "mshta.exe",
        "rundll32.exe",
        "regsvr32.exe",
    }
)
_MAX_WINDOWS_TEXT = 32767
_MAX_ARGUMENTS = 64
_MAX_ARGUMENT_TEMPLATE = 8192
_PROGID_RE = re.compile(r"[A-Za-z0-9_.\\-]{1,512}\Z")
_PROFILE_ID_RE = re.compile(r"[A-Za-z0-9 _.-]{1,128}\Z")


class WindowsOpenActionModule:
    def __init__(
        self,
        configured_actions: tuple[dict[str, object], ...] = (),
        *,
        profile_labels: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._configured_actions = tuple(
            OpenAction.model_validate(raw) for raw in configured_actions
        )
        self._profile_labels = profile_labels or {}

    async def discover(self, target: OpenTarget) -> tuple[OpenAction, ...]:
        return await asyncio.to_thread(self._discover_sync, target)

    async def launch(self, action: OpenAction, context: ContextEnvelope) -> None:
        await asyncio.to_thread(self._launch_sync, action, context)

    def _discover_sync(self, target: OpenTarget) -> tuple[OpenAction, ...]:
        actions: dict[str, OpenAction] = {}
        discovered = (
            _file_association_actions(target.extension)
            if isinstance(target, FileTarget)
            else _protocol_actions(target.scheme)
        )
        for action in (*discovered, *_browser_profile_actions(target), *_vscode_actions(target)):
            actions[action.action_id] = action
        for action in self._configured_actions:
            if _compatible(action, target) and _launch_shape_valid(action):
                actions[action.action_id] = action
        labelled: list[OpenAction] = []
        for action in actions.values():
            if not _compatible(action, target) or not _launch_shape_valid(action):
                continue
            labels = self._profile_labels.get(action.action_id, ())
            if labels:
                description = action.capability_description + " User labels: " + ", ".join(labels)
                action = action.model_copy(update={"capability_description": description})
            labelled.append(action)
        return tuple(
            sorted(
                labelled,
                key=lambda item: (
                    not bool(item.invocation_data.get("is_default")),
                    item.display_name.casefold(),
                    item.action_id,
                ),
            )[:255]
        )

    @staticmethod
    def _launch_sync(action: OpenAction, context: ContextEnvelope) -> None:
        if not any(candidate == action for candidate in context.open_actions):
            raise LaunchFailed("The selected action is not in this request's candidate set")
        if not _compatible(action, context.target) or not _launch_shape_valid(action):
            raise LaunchFailed("The selected action is incompatible or has an unsafe launch shape")
        if str(context.request.verb) not in action.invocation_data.get("supported_verbs", ["open"]):
            raise LaunchFailed("The selected action does not support this verb")
        if context.request.parameters:
            raise LaunchFailed("Additional Shell parameters cannot be preserved by this action")
        if (
            context.request.working_directory is not None
            and not _absolute_windows_text(str(context.request.working_directory))
        ):
            raise LaunchFailed("The selected working directory is not an absolute path")
        target = (
            str(context.target.path)
            if isinstance(context.target, FileTarget)
            else context.request.target
        )
        if not _launch_target_valid(target, context.target):
            raise LaunchFailed("The selected target is not a valid absolute Windows path or URI")
        if action.invocation_kind == "assoc_handler":
            _invoke_progid(str(action.invocation_data["progid"]), target, context)
            return
        if action.invocation_kind == "packaged_app":
            raise LaunchFailed("Explicit packaged-app invocation is not implemented")
        executable = _verified_executable(str(action.invocation_data.get("executable", "")))
        if executable is None:
            raise LaunchFailed("The selected executable is missing or is a script host")
        profile_directory = action.invocation_data.get("profile_directory")
        if profile_directory is not None and _verified_profile_directory(
            profile_directory, require_directory=True
        ) is None:
            raise LaunchFailed("The selected application profile no longer exists")
        arguments = [
            value.replace("{target}", target)
            for value in action.invocation_data.get("arguments", [])
        ]
        try:
            subprocess.Popen(
                [str(executable), *arguments],
                cwd=str(context.request.working_directory)
                if context.request.working_directory
                else None,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                close_fds=True,
                shell=False,
                env=sanitized_child_environment(),
            )
        except OSError as exc:
            raise LaunchFailed("The selected application could not be launched") from exc


def _compatible(action: OpenAction, target: OpenTarget) -> bool:
    data = action.invocation_data
    if isinstance(target, UrlTarget):
        schemes = data.get("schemes", [])
        return _contains_casefold(schemes, target.scheme)
    extensions = data.get("extensions", [])
    mime_types = data.get("mime_types", [])
    return (
        _contains_casefold(extensions, target.extension)
    ) or (
        bool(target.mime_type)
        and _contains_exact(mime_types, target.mime_type)
    )


def _launch_shape_valid(action: OpenAction) -> bool:
    data = action.invocation_data
    verbs = data.get("supported_verbs", ["open"])
    if (
        not isinstance(verbs, (list, tuple))
        or not verbs
        or any(not isinstance(v, str) for v in verbs)
        or any(v not in {"open", "edit"} for v in verbs)
        or len(set(verbs)) != len(verbs)
    ):
        return False
    if action.invocation_kind == "assoc_handler":
        progid = data.get("progid")
        return (
            isinstance(progid, str)
            and _PROGID_RE.fullmatch(progid) is not None
            and not any(part in {".", ".."} for part in progid.split("\\"))
        )
    if action.invocation_kind != "executable":
        return False
    arguments = data.get("arguments")
    if not _argument_template_valid(arguments):
        return False
    profile_directory = data.get("profile_directory")
    if profile_directory is not None and _verified_profile_directory(profile_directory) is None:
        return False
    executable = str(data.get("executable", ""))
    return (
        _absolute_windows_text(executable)
        and Path(executable).suffix.casefold() == ".exe"
        and (Path(executable).name.casefold() not in _SCRIPT_HOSTS)
    )


def _verified_executable(value: str) -> Path | None:
    if not isinstance(value, str) or not _absolute_windows_text(value):
        return None
    executable = Path(os.path.expandvars(value))
    if not executable.is_file() or executable.is_symlink():
        return None
    if executable.suffix.casefold() != ".exe" or executable.name.casefold() in _SCRIPT_HOSTS:
        return None
    return executable


def _absolute_windows_text(value: object) -> bool:
    """Validate a path-shaped value without relying on the host OS parser."""
    if not isinstance(value, str) or not value or len(value) > _MAX_WINDOWS_TEXT:
        return False
    if any(ord(character) < 32 for character in value):
        return False
    expanded = os.path.expandvars(value)
    return ntpath.isabs(expanded) or Path(expanded).is_absolute()


def _verified_profile_directory(value: object, *, require_directory: bool = False) -> Path | None:
    if not isinstance(value, str) or not _absolute_windows_text(value):
        return None
    directory = Path(os.path.expandvars(value))
    if directory.is_symlink() or (require_directory and not directory.is_dir()):
        return None
    return directory


def _argument_template_valid(arguments: object) -> bool:
    if not isinstance(arguments, (list, tuple)) or not arguments:
        return False
    if len(arguments) > _MAX_ARGUMENTS or not all(isinstance(value, str) for value in arguments):
        return False
    if sum(value.count("{target}") for value in arguments) != 1:
        return False
    return (
        all(
            value
            and len(value) <= _MAX_ARGUMENT_TEMPLATE
            and not any(ord(character) < 32 for character in value)
            for value in arguments
        )
        and sum(len(value) for value in arguments) <= _MAX_WINDOWS_TEXT
    )


def _contains_casefold(values: object, expected: str) -> bool:
    return (
        isinstance(values, (list, tuple))
        and any(
            isinstance(value, str) and value.casefold() == expected.casefold()
            for value in values
        )
    )


def _contains_exact(values: object, expected: str | None) -> bool:
    return (
        expected is not None
        and isinstance(values, (list, tuple))
        and any(isinstance(value, str) and value == expected for value in values)
    )


def _launch_target_valid(target: str, value: OpenTarget) -> bool:
    if not isinstance(target, str) or not target or len(target) > _MAX_WINDOWS_TEXT:
        return False
    if any(ord(character) < 32 for character in target):
        return False
    if isinstance(value, FileTarget):
        return _absolute_windows_text(target)
    return True


def _registry_value(hive: object, path: str, name: str = "") -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(hive, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value if isinstance(value, str) else None
    except OSError:
        return None


def _registry_value_names(hive: object, path: str) -> list[str]:
    if winreg is None:
        return []
    try:
        with winreg.OpenKey(hive, path) as key:
            names = []
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, _, _ = winreg.EnumValue(key, index)
                names.append(name)
            return names
    except OSError:
        return []


def _user_choice(target_key: str, *, protocol: bool) -> str | None:
    if winreg is None:
        return None
    prefix = (
        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
        if protocol
        else r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
    )
    return _registry_value(
        winreg.HKEY_CURRENT_USER, prefix + "\\" + target_key + r"\UserChoice", "ProgId"
    )


def _file_association_actions(extension: str) -> list[OpenAction]:
    if winreg is None or not extension:
        return []
    current = _user_choice(extension, protocol=False)
    progids = [current, _registry_value(winreg.HKEY_CLASSES_ROOT, extension)]
    progids.extend(_registry_value_names(winreg.HKEY_CLASSES_ROOT, extension + r"\OpenWithProgids"))
    progids.extend(
        _registry_value_names(
            winreg.HKEY_CURRENT_USER,
            "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\"
            + extension
            + r"\OpenWithProgids",
        )
    )
    actions = []
    for progid in dict.fromkeys(value for value in progids if value):
        action = _progid_action(progid, extensions=[extension], is_default=progid == current)
        if action is not None:
            actions.append(action)
    return actions


def _protocol_actions(scheme: str) -> list[OpenAction]:
    if winreg is None:
        return []
    scheme = scheme.casefold()
    current = _user_choice(scheme, protocol=True)
    progids = [current]
    if _registry_value(winreg.HKEY_CLASSES_ROOT, scheme, "URL Protocol") is not None:
        progids.append(scheme)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for app_name in _registry_value_names(hive, r"Software\RegisteredApplications"):
            capabilities = _registry_value(hive, r"Software\RegisteredApplications", app_name)
            if capabilities:
                progids.append(_registry_value(hive, capabilities + r"\URLAssociations", scheme))
    actions = []
    for progid in dict.fromkeys(value for value in progids if value):
        action = _progid_action(progid, schemes=[scheme], is_default=progid == current)
        if action is not None:
            actions.append(action)
    return actions


def _progid_action(
    progid: str,
    *,
    extensions: list[str] | None = None,
    schemes: list[str] | None = None,
    is_default: bool = False,
) -> OpenAction | None:
    if winreg is None:
        return None
    if _PROGID_RE.fullmatch(progid) is None:
        return None
    display = _safe_display_name(
        _registry_value(winreg.HKEY_CLASSES_ROOT, progid), progid
    )
    verbs = []
    for verb in ("open", "edit"):
        command = _registry_value(winreg.HKEY_CLASSES_ROOT, progid + rf"\shell\{verb}\command")
        if command:
            argv = _command_line_to_argv(os.path.expandvars(command))
            if _command_argv_valid(argv) and _verified_executable(argv[0]) is not None:
                verbs.append(verb)
    if not verbs:
        return None
    return OpenAction(
        action_id="assoc." + hashlib.sha256(progid.encode()).hexdigest()[:16],
        application_id=progid,
        display_name=display,
        capability_description=f"Windows registered handler {progid}.",
        invocation_kind="assoc_handler",
        invocation_data={
            "progid": progid,
            "supported_verbs": verbs,
            "extensions": extensions or [],
            "schemes": schemes or [],
            "is_default": is_default,
        },
    )


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _invoke_progid(progid: str, target: str, context: ContextEnvelope) -> None:
    if os.name != "nt" or winreg is None:
        raise LaunchFailed("Registered handlers require Windows")
    if not _launch_target_valid(target, context.target) or not _PROGID_RE.fullmatch(progid):
        raise LaunchFailed("The selected registered handler has unsafe parameters")
    verb = str(context.request.verb)
    command = _registry_value(winreg.HKEY_CLASSES_ROOT, progid + rf"\shell\{verb}\command")
    argv = _command_line_to_argv(os.path.expandvars(command or ""))
    if not _command_argv_valid(argv) or _verified_executable(argv[0]) is None:
        raise LaunchFailed("The selected handler changed or is no longer available")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    execute = shell.ShellExecuteExW
    execute.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    execute.restype = wintypes.BOOL
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000001 | 0x00000400  # SEE_MASK_CLASSNAME | SEE_MASK_FLAG_NO_UI
    info.lpClass = progid
    info.lpVerb = verb
    info.lpFile = target
    info.lpDirectory = (
        str(context.request.working_directory) if context.request.working_directory else None
    )
    info.nShow = context.request.show_command
    if not execute(ctypes.byref(info)):
        raise LaunchFailed(f"Selected registered handler failed ({ctypes.get_last_error()})")


def _browser_profile_actions(target: OpenTarget) -> list[OpenAction]:
    if not isinstance(target, UrlTarget) or target.scheme.casefold() not in {"http", "https"}:
        return []
    local_value = os.environ.get("LOCALAPPDATA")
    if not local_value:
        return []
    local = Path(local_value)
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    roots = [
        (
            "chrome",
            [
                program_files / "Google/Chrome/Application/chrome.exe",
                local / "Google/Chrome/Application/chrome.exe",
            ],
            local / "Google/Chrome/User Data/Local State",
        ),
        (
            "edge",
            [
                program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
                program_files / "Microsoft/Edge/Application/msedge.exe",
            ],
            local / "Microsoft/Edge/User Data/Local State",
        ),
    ]
    actions = []
    for browser_id, locations, local_state in roots:
        executable = next((item for item in locations if item.is_file()), None)
        if (
            executable is None
            or not local_state.is_file()
            or local_state.is_symlink()
            or local_state.stat().st_size > 8_388_608
        ):
            continue
        try:
            profiles = (
                json.loads(local_state.read_text(encoding="utf-8"))
                .get("profile", {})
                .get("info_cache", {})
            )
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(profiles, dict):
            continue
        for profile_id, details in profiles.items():
            if not isinstance(details, dict) or not re.fullmatch(
                r"[A-Za-z0-9 _.-]{1,128}", profile_id
            ):
                continue
            if profile_id in {".", ".."}:
                continue
            profile_directory = local_state.parent / profile_id
            if profile_directory.is_symlink() or not profile_directory.is_dir():
                continue
            name = _safe_display_name(details.get("name"), profile_id)
            profile_slug = _slug(profile_id)
            actions.append(
                OpenAction(
                    action_id=f"{browser_id}.{profile_slug}",
                    application_id=browser_id,
                    profile_id=profile_id,
                    display_name=f"{browser_id.title()} - {name}",
                    capability_description=(
                        f"{browser_id.title()} browser profile {name} for web links."
                    ),
                    invocation_kind="executable",
                    invocation_data={
                        "executable": str(executable),
                        "arguments": [f"--profile-directory={profile_id}", "{target}"],
                        "profile_directory": str(profile_directory),
                        "schemes": ["http", "https"],
                        "supported_verbs": ["open"],
                    },
                )
            )
    return actions


def _vscode_actions(target: OpenTarget) -> list[OpenAction]:
    if not isinstance(target, FileTarget) or target.extension.casefold() not in _TEXT_EXTENSIONS:
        return []
    candidates: list[Path] = []
    for environment, suffix in (
        ("LOCALAPPDATA", "Programs/Microsoft VS Code/Code.exe"),
        ("PROGRAMFILES", "Microsoft VS Code/Code.exe"),
    ):
        root = os.environ.get(environment)
        if root:
            candidates.append(Path(root) / suffix)
    found = shutil.which("Code.exe")
    if found:
        candidates.append(Path(found))
    executable = next((item for item in candidates if item.is_file()), None)
    if executable is None:
        return []
    return [
        OpenAction(
            action_id="vscode.default",
            application_id="vscode",
            display_name="Visual Studio Code",
            capability_description="Text, source code, CSV datasets, JSON, and notebooks.",
            invocation_kind="executable",
            invocation_data={
                "executable": str(executable),
                "arguments": ["--", "{target}"],
                "extensions": sorted(_TEXT_EXTENSIONS),
                "supported_verbs": ["open", "edit"],
            },
        )
    ]


def _command_line_to_argv(command: str) -> list[str]:
    if os.name != "nt" or not command:
        return []
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    parse = shell.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = kernel.LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    argc = ctypes.c_int()
    argv = parse(command, ctypes.byref(argc))
    if not argv:
        return []
    try:
            return [argv[index] for index in range(argc.value)]
    finally:
        free(ctypes.cast(argv, ctypes.c_void_p))


def _slug(value: str) -> str:
    slug = "".join(char.casefold() if char.isalnum() else "-" for char in value).strip("-")
    return slug or hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _command_argv_valid(argv: object) -> bool:
    return (
        isinstance(argv, list)
        and bool(argv)
        and len(argv) <= _MAX_ARGUMENTS
        and all(
            isinstance(value, str)
            and value
            and len(value) <= _MAX_ARGUMENT_TEMPLATE
            and not any(ord(character) < 32 for character in value)
            for value in argv
        )
        and sum(len(value) for value in argv) <= _MAX_WINDOWS_TEXT
    )


def _safe_display_name(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(character if ord(character) >= 32 else " " for character in value)
    return cleaned.strip()[:128] or fallback

from __future__ import annotations

import os
import sys
from pathlib import Path

_SAFE_EXTENSIONS = (
    ".csv", ".tsv", ".json", ".jsonl", ".xlsx", ".xls", ".docx", ".doc",
    ".pdf", ".txt", ".md", ".log", ".xml", ".yaml", ".yml", ".toml",
    ".py", ".js", ".ts", ".html", ".css", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".svg", ".mp4", ".mkv", ".mov", ".mp3", ".wav",
)


def _executable() -> Path:
    return Path(sys.executable).resolve()


def register_explorer() -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("Explorer integration requires Windows")
    import winreg

    executable = _executable()
    command = f'"{executable}" --open-target "%1"'

    def set_value(path: str, name: str | None, value: str, kind: int = winreg.REG_SZ) -> None:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, kind, value)

    base = r"Software\Classes"
    set_value(base + r"\*\shell\JevDefaultOpen", "MUIVerb", "使用 Jev 智能打开")
    set_value(base + r"\*\shell\JevDefaultOpen", "Icon", str(executable))
    set_value(base + r"\*\shell\JevDefaultOpen\command", None, command)

    progid = base + r"\JevDefaultOpen.File"
    set_value(progid, None, "Jev context-aware open target")
    set_value(progid + r"\DefaultIcon", None, f"{executable},0")
    set_value(progid + r"\shell\open\command", None, command)

    application = base + r"\Applications\JevDefaultOpen.exe"
    set_value(application, "FriendlyAppName", "Jev Default Open")
    set_value(application + r"\shell\open\command", None, command)
    for extension in _SAFE_EXTENSIONS:
        set_value(application + r"\SupportedTypes", extension, "")
        set_value(base + rf"\{extension}\OpenWithProgids", "JevDefaultOpen.File", "")

    capabilities = r"Software\JevDefaultOpen\Capabilities"
    set_value(capabilities, "ApplicationName", "Jev Default Open")
    set_value(capabilities, "ApplicationDescription", "Context-aware application selection")
    for extension in _SAFE_EXTENSIONS:
        set_value(capabilities + r"\FileAssociations", extension, "JevDefaultOpen.File")
    set_value(
        r"Software\RegisteredApplications",
        "Jev Default Open",
        capabilities,
    )
    return {"registered": True, "executable": str(executable), "extensions": len(_SAFE_EXTENSIONS)}


def unregister_explorer() -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("Explorer integration requires Windows")
    import winreg

    def delete_tree(root, path: str) -> None:
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                while True:
                    try:
                        child = winreg.EnumKey(key, 0)
                    except OSError:
                        break
                    delete_tree(root, path + "\\" + child)
            winreg.DeleteKey(root, path)
        except FileNotFoundError:
            pass

    for path in (
        r"Software\Classes\*\shell\JevDefaultOpen",
        r"Software\Classes\JevDefaultOpen.File",
        r"Software\Classes\Applications\JevDefaultOpen.exe",
        r"Software\JevDefaultOpen",
    ):
        delete_tree(winreg.HKEY_CURRENT_USER, path)
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\RegisteredApplications",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, "Jev Default Open")
    except FileNotFoundError:
        pass
    return {"registered": False}

"""Install / uninstall WireSpot (per-user, no MSI).

Install (WireSpotSetup.exe):
  * program files  -> %LOCALAPPDATA%\\Programs\\WireSpot (WireSpot.exe,
                      WireSpotCLI.exe, install.json marker)
  * your data      -> %APPDATA%\\WireSpot (settings.json, vpn\\, logs\\)
  * one shortcut on the desktop to the app (the CLI opens from the app),
    a Start-menu entry, and an "Apps & features" entry that runs
    ``WireSpot.exe --uninstall``.
  * A first install next to an old portable folder moves its profiles over
    (moved, not copied: private keys stay in one place).

Uninstall (from the app's Settings, or Apps & features):
  * stops the WireSpot session first (only WireSpot's own tunnel, hotspot
    and DNS lock - nothing else on the system)
  * removes the logon task, shortcuts, uninstall entry, %ProgramData%\\WireSpot
    runtime data and the program folder
  * keep_data=True keeps settings.json, vpn\\ and approved devices;
    keep_data=False deletes %APPDATA%\\WireSpot too.
Deletion is scoped: only folders that are provably WireSpot's (install.json
marker, the exact %APPDATA%/%ProgramData% WireSpot folders) are removed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import APP_NAME, VERSION, autostart, paths

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\WireSpot"
EXES = ("WireSpot.exe", "WireSpotCLI.exe")
Progress = Callable[[str], None]


def _ps(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", script], capture_output=True, text=True, errors="replace", timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def special_folder(name: str) -> Path | None:
    """'Desktop' | 'Programs' (Start menu) - follows OneDrive redirection."""
    if os.environ.get("WIRESPOT_SHORTCUT_DIR"):
        return Path(os.environ["WIRESPOT_SHORTCUT_DIR"])
    try:
        r = _ps(f"[Environment]::GetFolderPath({_q(name)})", 20)
        out = r.stdout.strip()
        return Path(out) if out else None
    except (OSError, subprocess.SubprocessError):
        return None


def shortcut_path(folder: str) -> Path | None:
    d = special_folder(folder)
    return d / f"{APP_NAME}.lnk" if d else None


def shortcut_paths() -> list[Path]:
    return [p for p in (shortcut_path("Desktop"), shortcut_path("Programs")) if p]


def installed_version(dest: Path | None = None) -> str:
    """Version of an existing installation ('' if none)."""
    try:
        return str(json.loads(((dest or paths.install_dir()) / paths.INSTALL_MARKER).read_text(encoding="utf-8"))
                   .get("version", "?"))
    except (OSError, ValueError):
        return ""


def app_running() -> bool:
    if os.name != "nt":
        return False
    import ctypes

    from .tray import WINDOW_CLASS

    return bool(ctypes.windll.user32.FindWindowW(WINDOW_CLASS, None))


class AppStillRunning(RuntimeError):
    pass


def terminate_running() -> None:
    """Last resort for an older WireSpot that does not understand 'quit' (0.2.0 and earlier).
    Only the app process is ended; the VPN tunnel and hotspot are Windows services and keep running."""
    subprocess.run(["taskkill", "/F", "/T", "/IM", "WireSpot.exe"], capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    time.sleep(1.5)


def make_shortcut(lnk: Path, target: Path, workdir: Path) -> bool:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    r = _ps(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_q(lnk)});"
            f"$s.TargetPath={_q(target)};$s.WorkingDirectory={_q(workdir)};$s.IconLocation={_q(str(target) + ',0')};"
            f"$s.Description='WireSpot - WireGuard x Mobile Hotspot';$s.Save()")
    return r.returncode == 0 and lnk.exists()


# ===================================================================== registry
def register(dest: Path) -> None:
    import winreg

    exe = dest / "WireSpot.exe"
    size_kb = sum(f.stat().st_size for f in dest.glob("*.exe")) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", VERSION), ("Publisher", APP_NAME),
                            ("DisplayIcon", f'"{exe}",0'), ("InstallLocation", str(dest)),
                            ("UninstallString", f'"{exe}" --uninstall'),
                            ("Comments", "WireGuard x Mobile Hotspot")):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)


def unregister() -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


# ===================================================================== running app
def close_running_app(timeout: float = 12.0) -> bool:
    """Ask a running WireSpot app to quit (VPN/hotspot keep running). True when gone."""
    if os.name != "nt":
        return True
    import ctypes

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    from .tray import WINDOW_CLASS, WM_QUITAPP

    hwnd = user32.FindWindowW(WINDOW_CLASS, None)
    if not hwnd:
        return True
    user32.PostMessageW(ctypes.c_void_p(hwnd), WM_QUITAPP, 0, 0)
    end = time.time() + timeout
    while time.time() < end:
        if not user32.FindWindowW(WINDOW_CLASS, None):
            time.sleep(1.0)                  # the onefile launcher exits right after its child
            return True
        time.sleep(0.2)
    return False


def _copy_retry(src: Path, dst: Path, tries: int = 25) -> None:
    for i in range(tries):
        try:
            tmp = dst.with_suffix(dst.suffix + ".new")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.4)


# ===================================================================== migration
def find_legacy(near: Path) -> Path | None:
    """An old portable WireSpot folder next to the setup file (settings.json / vpn\\*.conf)."""
    for d in (near, near / "portable", near.parent / "portable"):
        try:
            if d.resolve() == paths.appdata_dir().resolve():
                continue
            if (d / "vpn").is_dir() and any((d / "vpn").glob("*.conf")) or (d / "settings.json").is_file():
                return d
        except OSError:
            continue
    return None


def migrate(src: Path, data: Path, progress: Progress) -> int:
    moved = 0
    data.mkdir(parents=True, exist_ok=True)
    (data / "vpn").mkdir(exist_ok=True)
    if (src / "settings.json").is_file() and not (data / "settings.json").exists():
        shutil.copy2(src / "settings.json", data / "settings.json")
        progress(f"Settings brought over from {src.name}")
    for conf in sorted((src / "vpn").glob("*.conf")) if (src / "vpn").is_dir() else []:
        dest = data / "vpn" / conf.name
        if dest.exists():
            continue
        shutil.move(str(conf), str(dest))          # move: the private key stays in one place
        moved += 1
    if moved:
        progress(f"Moved {moved} VPN profile{'s' if moved != 1 else ''} from {src.name}\\vpn")
    return moved


# ===================================================================== install
def install(payload: Path, dest: Path | None = None, data: Path | None = None, progress: Progress = lambda s: None,
            legacy_near: Path | None = None, shortcuts: bool = True, registry: bool = True, desktop: bool = True,
            start_menu: bool = True, start_with_windows: bool | None = None, migrate_legacy: bool = True,
            force_close: bool = False, accepted: dict | None = None) -> dict:
    """``start_with_windows``: True/False sets it, None keeps whatever it was (re-pointed at this copy)."""
    dest = dest or paths.install_dir()
    data = data or paths.appdata_dir()
    result = {"dest": str(dest), "data": str(data), "moved": 0, "shortcuts": []}
    sandbox = bool(os.environ.get("WIRESPOT_SANDBOX"))      # smoke tests: no registry, no logon task
    if not sandbox:
        progress("Closing a running WireSpot (your VPN and hotspot keep running)")
        if not close_running_app():
            if not force_close:
                raise AppStillRunning("An older WireSpot is running and did not close.")
            terminate_running()
            if app_running():
                raise RuntimeError("WireSpot is still running. Quit it from the tray and run setup again.")
    progress("Copying WireSpot")
    dest.mkdir(parents=True, exist_ok=True)
    for name in EXES:
        src = payload / name
        if not src.is_file():
            raise FileNotFoundError(f"{name} is missing from the setup package")
        _copy_retry(src, dest / name)
    marker = {"app": APP_NAME, "version": VERSION, "dir": str(dest),
              "data": str(data), "installed": time.time()}
    if accepted is not None:
        marker["accepted"] = accepted
    (dest / paths.INSTALL_MARKER).write_text(json.dumps(marker, indent=1),
                                             encoding="utf-8")
    progress("Preparing your data folder")
    data.mkdir(parents=True, exist_ok=True)
    (data / "vpn").mkdir(exist_ok=True)
    if migrate_legacy and legacy_near is not None and not (data / "settings.json").exists():
        src = find_legacy(legacy_near)
        if src is not None:
            result["moved"] = migrate(src, data, progress)
    if not (data / "settings.json").exists():
        from . import settings as settings_mod

        settings_mod.save(settings_mod.load(data / "settings.json")[0], data / "settings.json")
    if shortcuts:
        for folder, wanted, label in (("Desktop", desktop, "desktop shortcut"), ("Programs", start_menu, "Start menu entry")):
            lnk = shortcut_path(folder)
            if lnk is None:
                continue
            if wanted:
                progress(f"Creating the {label}")
                if make_shortcut(lnk, dest / "WireSpot.exe", dest):
                    result["shortcuts"].append(str(lnk))
            elif lnk.exists():
                lnk.unlink(missing_ok=True)
    if registry and os.name == "nt" and not sandbox:
        register(dest)
    if not sandbox:
        try:
            if start_with_windows or (start_with_windows is None and autostart.is_enabled()):
                progress("Setting up Start with Windows")
                autostart.enable(str(dest / "WireSpot.exe"))       # also re-points an older copy's task
            elif start_with_windows is False and autostart.is_enabled():
                autostart.disable()
        except Exception as e:
            progress(f"Start with Windows was not changed ({e})")
    progress("Installed")
    return result


# ===================================================================== uninstall
def _rmtree(p: Path) -> bool:
    if not p.exists():
        return True
    shutil.rmtree(p, ignore_errors=True)
    return not p.exists()


def schedule_removal(dest: Path) -> None:
    """Delete the program files after this process exits (a running exe cannot delete itself)."""
    files = [dest / n for n in (*EXES, paths.INSTALL_MARKER)]
    lst = ",".join(_q(f) for f in files)
    script = (f"for($i=0;$i -lt 60;$i++){{Start-Sleep -Milliseconds 500;"
              f"Remove-Item -LiteralPath {lst} -Force -ErrorAction SilentlyContinue;"
              f"if(-not (Test-Path -LiteralPath {_q(files[0])})){{break}}}};"
              f"try{{[IO.Directory]::Delete({_q(dest)})}}catch{{}}")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                     creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if os.name == "nt" else 0,
                     close_fds=True)


def uninstall(keep_data: bool, relay=None, settings: dict | None = None, progress: Progress = lambda s: None,
              dest: Path | None = None, data: Path | None = None, schedule: bool = True) -> tuple[bool, str]:
    from . import ui

    dest = dest or paths.APP_DIR
    data = data or paths.appdata_dir()
    if not paths.is_installed(dest):
        return False, "This copy of WireSpot was not installed by setup (portable). Delete its folder instead."
    notes: list[str] = []
    # 1. stop WireSpot's own session - never leave a tunnel service pointing at deleted files
    if relay is not None:
        relay.reload()
        from .state import State

        if relay.m.state != State.DISCONNECTED or relay.record.tunnel_name:
            progress("Stopping the VPN and hotspot")
            if not relay.stop(settings):
                return False, "Could not stop the WireSpot session cleanly. Nothing was removed; see Activity."
        relay.stop_guard()
    progress("Removing Start with Windows")
    try:
        autostart.disable()
    except Exception as e:
        notes.append(f"logon task: {e}")
    progress("Removing shortcuts")
    for lnk in shortcut_paths():
        try:
            lnk.unlink(missing_ok=True)
        except OSError as e:
            notes.append(f"{lnk.name}: {e}")
    unregister()
    progress("Removing runtime data")
    pd = paths.PROGRAMDATA_DIR
    if pd.name.lower() == "wirespot" and pd.exists():
        for child in pd.iterdir():
            if keep_data and child.name == "devices.json":
                continue
            if child.is_dir():
                _rmtree(child)
            else:
                try:
                    child.unlink()
                except OSError as e:
                    notes.append(f"{child.name}: {e}")
        if not keep_data:
            try:
                pd.rmdir()
            except OSError:
                pass
    progress("Removing your data" if not keep_data else "Keeping your settings and VPN profiles")
    if data.name.lower() == "wirespot" and data.exists():
        if keep_data:
            _rmtree(data / "logs")
        elif not _rmtree(data):
            notes.append(f"could not remove {data}")
    progress("Removing the program")
    if schedule:
        schedule_removal(dest)
    ui.info("WireSpot uninstalled" + (" (settings and VPN profiles kept)" if keep_data else ""))
    msg = ("Settings and VPN profiles were kept in " + str(data)) if keep_data else "Everything was removed."
    if notes:
        msg += " Some items could not be removed: " + "; ".join(notes)
    return True, msg

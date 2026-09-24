"""Start the WireSpot tray at logon - elevated, without a UAC prompt.

A Registry Run key cannot launch an app that requires administrator rights,
so this uses a Task Scheduler logon task with RunLevel=HighestAvailable. The
task is defined in XML because the schtasks command-line defaults would
refuse to start on battery and kill the tray after 72 hours.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import paths

TASK_NAME = r"\WireSpot\WireSpot Tray"


def tray_command() -> tuple[str, str]:
    """(executable, arguments) that start the tray from this installation."""
    if getattr(sys, "frozen", False):
        return str(paths.exe("WireSpot.exe")), "--background --autostart"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw if pyw.exists() else Path(sys.executable)), f'"{paths.APP_DIR / "wirespot_app.py"}" --background --autostart'


def current_user() -> str:
    dom, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{user}" if dom else user


def task_xml(exe: str, args: str, user: str, delay: str = "PT15S") -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts WireSpot (WireGuard x Mobile Hotspot) in the tray at logon.</Description>
    <URI>{escape(TASK_NAME)}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>{delay}</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(Path(exe).parent))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace",
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def enable(exe: str | None = None) -> tuple[bool, str]:
    """Register the logon task (for exe, default: this installation's WireSpot.exe)."""
    if exe:
        args = "--background --autostart"
    else:
        exe, args = tray_command()
    if not Path(exe).exists():
        return False, f"{Path(exe).name} was not found next to WireSpot ({exe})."
    xml = task_xml(exe, args, current_user())
    fd, tmp = tempfile.mkstemp(suffix=".xml", prefix="wirespot-task-")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")
        r = _schtasks("/Create", "/TN", TASK_NAME, "/XML", tmp, "/F")
    finally:
        Path(tmp).unlink(missing_ok=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def disable() -> tuple[bool, str]:
    if not is_enabled():
        return True, "not enabled"
    r = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def is_enabled() -> bool:
    return _schtasks("/Query", "/TN", TASK_NAME).returncode == 0

"""Process execution: native commands and PowerShell scripts with a JSON protocol.

PowerShell scripts are written to a private temporary directory (never the
source tree), executed with -File, and deleted in a ``finally`` block. They
report results by printing one line ``@@WSJSON@@{...}``; everything else on
stdout/stderr is kept for diagnostics. Secrets are passed through environment
variables, never on the command line (which is visible to every local process
and would end up in logs).
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import log

MARKER = "@@WSJSON@@"
_tmp_dirs: set[str] = set()

PS_PRELUDE = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$script:Stage = 'init'
function Emit($obj) { '@@WSJSON@@' + ($obj | ConvertTo-Json -Compress -Depth 8) }
function NGuid($g) { if ($null -eq $g) { return '' } ; return ([string]$g).Trim('{}').ToLowerInvariant() }
'''

# WinRT from Windows PowerShell 5.1. AsTask overloads are selected by their
# parameter type rather than position so IAsyncOperationWithProgress`2 can
# never be picked by accident. Waits are bounded.
PS_WINRT = r'''
[void][System.Reflection.Assembly]::LoadWithPartialName('System.Runtime.WindowsRuntime')
$null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]
$script:AsTaskAction = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
$script:AsTaskOp = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await-Action($op, [int]$ms = 30000) {
    $t = $script:AsTaskAction.Invoke($null, @($op))
    if (-not $t.Wait($ms)) { throw "WinRT action timed out after $ms ms" }
}
function Await-Result($op, [Type]$type, [int]$ms = 45000) {
    $t = $script:AsTaskOp.MakeGenericMethod($type).Invoke($null, @($op))
    if (-not $t.Wait($ms)) { throw "WinRT operation timed out after $ms ms" }
    return $t.Result
}
'''


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    data: dict | list | None = None
    duration: float = 0.0
    timed_out: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def error_text(self) -> str:
        if isinstance(self.data, dict) and self.data.get("error"):
            return str(self.data["error"])
        text = (self.stderr or "").strip() or (self.stdout or "").strip()
        return _first_meaningful_line(text) or f"exit code {self.returncode}"


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith(("At ", "+ ", "+~", "~")) and "CategoryInfo" not in s and "FullyQualifiedErrorId" not in s:
            return s
    return ""


def parse_json_marker(stdout: str):
    """Return the payload of the last marker line, or None."""
    for line in reversed((stdout or "").splitlines()):
        idx = line.find(MARKER)
        if idx >= 0:
            try:
                return json.loads(line[idx + len(MARKER):])
            except json.JSONDecodeError:
                return None
    return None


def split_param_block(script: str) -> tuple[str, str]:
    """PowerShell requires param() to be the first statement; the prelude must
    be inserted after it. Returns (param_block, remainder)."""
    stripped = script.lstrip()
    if not stripped.lower().startswith("param("):
        return "", script
    depth, in_str = 0, ""
    for i, ch in enumerate(stripped):
        if in_str:
            if ch == in_str:
                in_str = ""
            continue
        if ch in ("'", '"'):
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return stripped[: i + 1] + "\n", stripped[i + 1:]
    raise ValueError("unbalanced param() block")


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run(cmd: list[str], timeout: int = 30, env: dict | None = None, name: str = "") -> Result:
    t0 = time.monotonic()
    label = name or Path(cmd[0]).name
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env, creationflags=_creationflags(),
        )
        r = Result(p.returncode, p.stdout or "", p.stderr or "", duration=time.monotonic() - t0)
    except subprocess.TimeoutExpired as e:
        r = Result(-1, str(e.stdout or ""), str(e.stderr or ""), duration=time.monotonic() - t0, timed_out=True)
    except FileNotFoundError as e:
        r = Result(-2, "", str(e), duration=time.monotonic() - t0)
    log.debug(f"exec {label} rc={r.returncode} t={r.duration:.2f}s timeout={r.timed_out}")
    if r.returncode != 0 and log.enabled():
        log.debug(f"exec {label} stderr: {r.stderr.strip()[:2000]}")
    return r


def _cleanup_tmp() -> None:
    for d in list(_tmp_dirs):
        shutil.rmtree(d, ignore_errors=True)
        _tmp_dirs.discard(d)


atexit.register(_cleanup_tmp)


def powershell(script: str, args: list[str] | None = None, *, name: str = "ps", timeout: int = 60,
               secrets: dict[str, str] | None = None, winrt: bool = False) -> Result:
    """Run a PowerShell script. ``secrets`` are exposed as $env:NAME only."""
    head, rest = split_param_block(script)
    body = head + PS_PRELUDE + (PS_WINRT if winrt else "") + rest
    tmpdir = tempfile.mkdtemp(prefix="wirespot-")
    _tmp_dirs.add(tmpdir)
    path = Path(tmpdir) / f"{name}.ps1"
    env = None
    if secrets:
        env = dict(os.environ)
        env.update(secrets)
    try:
        path.write_text(body, encoding="utf-8-sig")
        cmd = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
               "-ExecutionPolicy", "Bypass", "-File", str(path), *(args or [])]
        r = run(cmd, timeout=timeout, env=env, name=f"ps:{name}")
        r.data = parse_json_marker(r.stdout)
        if log.enabled():
            log.debug(f"ps:{name} args={args} json={json.dumps(r.data)[:4000] if r.data is not None else None}")
        return r
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        _tmp_dirs.discard(tmpdir)


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

"""Updates from GitHub: Settings → About → Check for updates.

The latest release is read from GitHub's API. Its WireSpot.exe is downloaded
into WireSpot's ProgramData folder, which only SYSTEM and Administrators may
write to, so nothing can swap the file before the app (which runs as
administrator) starts it. Its SHA-256 is checked against the checksum GitHub
publishes for it before it's kept. The app then quits and starts it with
``--update``, and the new version installs itself over this one.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import VERSION, paths

REPO = "svyixiu/WireSpot"
ASSET = "WireSpot.exe"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/".lower()
USER_AGENT = f"WireSpot/{VERSION}"
# what the app may see of a check (the download address and checksum stay in the engine)
PUBLIC = ("current", "latest", "available", "notes", "published_at", "size", "page")


class UpdateError(Exception):
    pass


class Cancelled(Exception):
    pass


def parse_version(text: str) -> tuple[int, int, int]:
    """'v0.4.1' -> (0, 4, 1). Raises ValueError for anything else."""
    core = text.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = core.split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a version: {text!r}")
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return nums[0], nums[1], nums[2]


def valid_sha256(text) -> str | None:
    s = str(text or "").strip().lower()
    return s if len(s) == 64 and all(c in "0123456789abcdef" for c in s) else None


def sha256_in_notes(notes: str) -> str | None:
    """Older releases only have the checksum in their notes ("SHA-256: `…`")."""
    at = notes.find("SHA-256")
    if at < 0:
        return None
    word = ""
    for c in notes[at:] + " ":
        if c in "0123456789abcdefABCDEF":
            word += c
            continue
        if len(word) == 64:
            return word.lower()
        word = ""
    return None


def updates_dir() -> Path:
    return paths.PROGRAMDATA_DIR / "updates"


def _open(url: str, timeout: float, accept: str = "*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return urllib.request.urlopen(req, timeout=timeout)


def check(current: str = VERSION, timeout: float = 20) -> dict:
    """What GitHub's latest release is, and whether it's newer than ``current``."""
    try:
        with _open(API, timeout, "application/vnd.github+json") as r:
            release = json.load(r)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"GitHub couldn't answer right now (HTTP {e.code}).") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", None) or e
        raise UpdateError(f"Couldn't reach GitHub: {reason}") from e

    tag = str(release.get("tag_name") or "")
    try:
        latest = parse_version(tag)
    except ValueError:
        raise UpdateError(f"The latest release has an unexpected version: {tag}") from None
    notes = str(release.get("body") or "")
    info = {"current": current, "latest": ".".join(map(str, latest)), "available": latest > parse_version(current),
            "notes": notes, "published_at": str(release.get("published_at") or ""), "size": 0,
            "page": str(release.get("html_url") or ""), "url": "", "sha256": ""}
    if not info["available"]:
        return info

    asset = next((a for a in release.get("assets") or [] if a.get("name") == ASSET), None)
    if asset is None:
        raise UpdateError(f"WireSpot {info['latest']} is out, but its release has no {ASSET} to download yet.")
    url = str(asset.get("browser_download_url") or "")
    # only files attached to this project's own releases
    if not url.lower().startswith(DOWNLOAD_PREFIX):
        raise UpdateError("The update's download address isn't one of WireSpot's releases, so it wasn't used.")
    digest = str(asset.get("digest") or "")
    sha = valid_sha256(digest[7:]) if digest.startswith("sha256:") else None
    sha = sha or sha256_in_notes(notes)
    if not sha:
        raise UpdateError("GitHub doesn't list a checksum for this update, so it can't be checked. "
                          "Download it from the website instead.")
    info.update(size=int(asset.get("size") or 0), url=url, sha256=sha)
    return info


def public(info: dict) -> dict:
    return {k: info[k] for k in PUBLIC}


def _prepare_dir() -> Path:
    d = updates_dir()
    d.mkdir(parents=True, exist_ok=True)
    from . import profiles, winexec

    # as administrator: lock the folder to SYSTEM + Administrators (a normal
    # program can't swap the file). Not as a normal user, who'd lock themselves out.
    if winexec.is_admin():
        profiles.harden_dir(d)
    return d


def download(info: dict, progress: Callable[[int, int], None], cancel: threading.Event,
             timeout: float = 30) -> Path:
    """Downloads the update ``check`` found, reporting (downloaded, total) bytes.
    Returns the checked file; raises Cancelled when ``cancel`` is set."""
    d = _prepare_dir()
    part = d / f"WireSpot-{info['latest']}.exe.part"
    done = d / f"WireSpot-{info['latest']}.exe"
    try:
        # the timeout applies to each read too, so a stalled download ends instead of hanging
        with _open(info["url"], timeout) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            h = hashlib.sha256()
            downloaded, last = 0, 0.0
            progress(0, total)
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last >= 0.1:
                    last = now
                    progress(downloaded, total)
            progress(downloaded, total)
        if h.hexdigest() != info["sha256"]:
            raise UpdateError("The downloaded file doesn't match the checksum GitHub lists for it, "
                              "so it wasn't used. Try again.")
        done.unlink(missing_ok=True)
        part.rename(done)
        return done
    except (Cancelled, UpdateError):
        part.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"GitHub couldn't send the file (HTTP {e.code}).") from e
    except (urllib.error.URLError, OSError) as e:
        part.unlink(missing_ok=True)
        reason = getattr(e, "reason", None) or e
        raise UpdateError(f"The download stopped: {reason}") from e


def previous_acceptance(dest: Path | None = None) -> dict | None:
    """When the installed copy's Terms and Privacy Policy were agreed to (kept across updates)."""
    try:
        marker = json.loads(((dest or paths.install_dir()) / paths.INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    accepted = marker.get("accepted")
    return accepted if isinstance(accepted, dict) else None


def clean_downloads() -> None:
    """Downloads that were installed (or given up on) go when the installed app starts again."""
    shutil.rmtree(updates_dir(), ignore_errors=True)

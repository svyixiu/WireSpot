"""Listens for new WireGuard .conf files and offers to import them.

Three ways in:
  * save/download a .conf into the Downloads folder (watched in the background),
  * drag a .conf onto the WireSpot window (the console pastes its path),
  * ``import <path>``.

Every candidate is treated as untrusted: size-limited, strictly parsed, and
shown on a review card before anything is copied. The review card shows all
config fields except the PrivateKey itself - only that it is present/valid
and the public key derived from it.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import paths, profiles, ui
from .ui import Choice

POLL_SECONDS = 2.0
RECENT_SECONDS = 15 * 60          # files downloaded shortly before launch still count
DECLINED_PATH = paths.PROGRAMDATA_DIR / "declined.json"


def downloads_dir() -> Path:
    """Known-folder lookup (handles a relocated Downloads folder)."""
    if os.name == "nt":
        try:
            guid = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")
            buf = ctypes.c_void_p()
            gbytes = (ctypes.c_byte * 16).from_buffer_copy(guid.bytes_le)
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(gbytes), 0, None, ctypes.byref(buf)) == 0:
                path = ctypes.wstring_at(buf.value)
                ctypes.windll.ole32.CoTaskMemFree(buf)
                return Path(path)
        except Exception:
            pass
    return Path.home() / "Downloads"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def looks_like_wireguard(data: bytes) -> bool:
    head = data[:profiles.MAX_CONF_BYTES].decode("utf-8", "ignore").lower()
    return "[interface]" in head and "privatekey" in head and "[peer]" in head


def path_from_input(raw: str) -> Path | None:
    """Recognise a dragged-and-dropped path (possibly quoted, possibly with & prefix)."""
    s = raw.strip()
    s = re.sub(r"^&\s*", "", s)                      # PowerShell-style drop: & 'C:\x.conf'
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    if not s.lower().endswith(".conf"):
        return None
    p = Path(s)
    return p if p.is_file() else None


@dataclass
class Inspection:
    profile: "profiles.Profile | None" = None
    error: str = ""
    duplicate: Path | None = None


@dataclass
class Candidate:
    path: Path
    origin: str            # "Downloads", "drag & drop", "import", "vpn folder"
    sha: str


class Inbox:
    def __init__(self, vpn_dir: Path | None = None, watch_dirs: list[Path] | None = None):
        self.vpn_dir = vpn_dir or paths.VPN_DIR
        self.watch_dirs = watch_dirs if watch_dirs is not None else [downloads_dir()]
        self.pending: "queue.Queue[Candidate]" = queue.Queue()
        self._seen: set[tuple[str, float]] = set()
        self._sizes: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = time.time()
        self.declined: set[str] = self._load_declined()
        self.on_new = None          # callback(Candidate) for the notification line

    # ------------------------------------------------------------ persistence
    def _load_declined(self) -> set[str]:
        try:
            return set(json.loads(DECLINED_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return set()

    def _save_declined(self) -> None:
        try:
            DECLINED_PATH.parent.mkdir(parents=True, exist_ok=True)
            DECLINED_PATH.write_text(json.dumps(sorted(self.declined)), encoding="utf-8")
        except OSError:
            pass

    def known_digests(self) -> dict[str, Path]:
        out = {}
        for p in self.vpn_dir.glob("*.conf"):
            try:
                out[digest(p.read_bytes())] = p
            except OSError:
                pass
        return out

    # ------------------------------------------------------------ watching
    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="wirespot-inbox")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    @property
    def watching(self) -> bool:
        return self._thread is not None

    def _run(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                self.scan_once()
            except Exception:
                pass

    def scan_once(self) -> list[Candidate]:
        found = []
        known = None
        for d in self.watch_dirs:
            if not d.is_dir():
                continue
            for p in d.glob("*.conf"):
                try:
                    st = p.stat()
                except OSError:
                    continue
                key = (str(p).lower(), st.st_mtime)
                if key in self._seen or st.st_size > profiles.MAX_CONF_BYTES:
                    continue
                if st.st_mtime < self._started - RECENT_SECONDS:
                    self._seen.add(key)
                    continue
                # Wait until the size is stable across two polls (download finished).
                if self._sizes.get(key[0]) != st.st_size:
                    self._sizes[key[0]] = st.st_size
                    continue
                self._seen.add(key)
                try:
                    data = p.read_bytes()
                except OSError:
                    continue
                if not looks_like_wireguard(data):
                    continue
                sha = digest(data)
                if known is None:
                    known = self.known_digests()
                if sha in self.declined or sha in known:
                    continue
                c = Candidate(p, d.name if d != self.vpn_dir else "vpn folder", sha)
                self.pending.put(c)
                found.append(c)
                if self.on_new:
                    self.on_new(c)
        return found

    def take_pending(self) -> list[Candidate]:
        out = []
        while True:
            try:
                out.append(self.pending.get_nowait())
            except queue.Empty:
                return out

    # ------------------------------------------------------------ review / import
    def inspect(self, cand: Candidate) -> "Inspection":
        """Validate a candidate without printing (used by the desktop app and review())."""
        try:
            data = cand.path.read_bytes()
        except OSError as e:
            return Inspection(error=f"cannot read the file: {e}")
        cand.sha = cand.sha or digest(data)
        dup = self.known_digests().get(cand.sha)
        if dup:
            return Inspection(duplicate=dup)
        try:
            if len(data) > profiles.MAX_CONF_BYTES:
                raise profiles.ConfigError("file is too large")
            cfg = profiles.parse_conf(data.decode("utf-8-sig"))
        except (profiles.ConfigError, UnicodeDecodeError) as e:
            return Inspection(error=str(e))
        return Inspection(profile=profiles.Profile(
            path=cand.path, config=cfg,
            server=profiles.detect_server(cfg.peers[0].comments + cfg.header_comments + cfg.interface.comments, cand.path.stem),
            features=[c for c in cfg.interface.comments if "=" in c],
        ))

    def decline(self, cand: Candidate) -> None:
        if cand.sha:
            self.declined.add(cand.sha)
            self._save_declined()

    def review(self, cand: Candidate, decide=ui.choose, settings: dict | None = None, save_settings=None) -> str:
        """Show the review card and act on Accept/Decline. Returns 'accepted'|'declined'|'invalid'|'duplicate'."""
        info = self.inspect(cand)
        if info.duplicate:
            ui.info(f"{cand.path.name} is already imported as {info.duplicate.name}.")
            return "duplicate"
        if info.error:
            ui.blank()
            ui.rule("Rejected WireGuard file")
            ui.kv("File", f"{cand.path.name} ({cand.origin})", 12)
            ui.err(f"Not importable: {info.error}")
            ui.rule()
            return "invalid"
        self.card(info.profile, cand)
        if not ui.INTERACTIVE and not ui.AUTO_YES:
            # Importing a credential file must be a human decision.
            ui.info("Not imported: run WireSpot interactively to accept or decline this file.")
            return "declined"
        pick = decide("Add this profile to WireSpot?", [
            Choice("move", "Accept - move into WireSpot", "Removes the copy from Downloads so the private key lives in one place."),
            Choice("copy", "Accept - copy, keep the original"),
            Choice("decline", "Decline", "WireSpot will not ask about this file again."),
        ], 0 if cand.origin != "vpn folder" else 1, cancel="decline")
        if pick == "decline":
            self.declined.add(cand.sha)
            self._save_declined()
            ui.info("Declined. Nothing was copied.")
            return "declined"
        dest = self.import_file(cand.path, move=(pick == "move"))
        ui.ok(f"Imported as {dest.name}")
        if settings is not None and not settings["vpn"].get("default_profile"):
            settings["vpn"]["default_profile"] = dest.name
            if save_settings:
                save_settings(settings)
            ui.ok("Set as the default profile.")
        return "accepted"

    def card(self, p: profiles.Profile, cand: Candidate, title: str = "New WireGuard profile") -> None:
        iv = p.config.interface.values
        peer = p.config.peers[0].values
        w = 14
        ui.blank()
        ui.rule(title)
        ui.kv("File", f"{cand.path.name}  ({cand.origin})", w)
        server = p.server_name or "(unnamed)"
        where = (f"Secure Core {p.server.entry_country_code}→{p.country_code}" if p.server.entry_country_code
                 else p.country or "unknown country")
        tags = [t for t, on in (("free", p.server.free), ("Tor", p.server.tor)) if on]
        ui.kv("Server", f"{server} · {where}" + (f" · {', '.join(tags)}" if tags else ""), w)
        ui.kv("Endpoint", peer.get("endpoint", "-"), w)
        ui.kv("Address", iv.get("address", "-"), w)
        ui.kv("DNS", iv.get("dns", "-"), w)
        allowed = peer.get("allowedips", "")
        ui.kv("AllowedIPs", allowed + ("  (full tunnel)" if profiles.has_default_route(profiles._split_list(allowed)) else ""), w)
        ui.kv("Server key", peer.get("publickey", "-"), w)
        ui.kv("PresharedKey", "present" if "presharedkey" in peer else "none", w)
        if "persistentkeepalive" in peer:
            ui.kv("Keepalive", peer["persistentkeepalive"] + " s", w)
        for k in ("listenport", "mtu"):
            if k in iv:
                ui.kv(profiles.CANONICAL[k], iv[k], w)
        ui.kv("PrivateKey", f"{ui.mask('x' * 12)} valid 32-byte key (never displayed)", w)
        ui.kv("Your pub key", p.public_key, w)
        if p.key_name:
            ui.kv("Proton key", p.key_name, w)
        if p.features:
            ui.kv("Options", " · ".join(p.features), w)
        if len(p.config.peers) > 1:
            ui.kv("Peers", str(len(p.config.peers)), w)
        ui.kv("Checks", "no PreUp/PostUp scripts · keys valid · addresses valid · only standard keys", w)
        ui.rule()

    def import_file(self, src: Path, move: bool) -> Path:
        self.vpn_dir.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9_=+.#-]", "_", src.stem)[:48] or "profile"
        dest = self.vpn_dir / f"{stem}.conf"
        n = 2
        while dest.exists():
            dest = self.vpn_dir / f"{stem}-{n}.conf"
            n += 1
        if src.resolve() == dest.resolve():
            return dest
        if move:
            try:
                os.replace(src, dest)
            except OSError:
                shutil.copy2(src, dest)
                try:
                    src.unlink()
                except OSError:
                    ui.warn(f"Copied, but could not delete the original {src}.")
        else:
            shutil.copy2(src, dest)
        return dest

"""WireGuard .conf parsing, validation, Proton metadata and runtime copies.

Imported configs are untrusted input. They are parsed with a strict
whitelist (only keys wireguard-windows understands, no lifecycle hooks), and
the runtime copy handed to the tunnel service is *re-serialised* from the
parsed model - comments and anything unexpected never reach the service.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import TUNNEL_PREFIX

MAX_CONF_BYTES = 64 * 1024

COUNTRIES = {
    "AE": "United Arab Emirates", "AL": "Albania", "AR": "Argentina", "AT": "Austria", "AU": "Australia",
    "BA": "Bosnia and Herzegovina", "BE": "Belgium", "BG": "Bulgaria", "BR": "Brazil", "CA": "Canada",
    "CH": "Switzerland", "CL": "Chile", "CO": "Colombia", "CR": "Costa Rica", "CY": "Cyprus", "CZ": "Czechia",
    "DE": "Germany", "DK": "Denmark", "EE": "Estonia", "EG": "Egypt", "ES": "Spain", "FI": "Finland",
    "FR": "France", "GB": "United Kingdom", "UK": "United Kingdom", "GE": "Georgia", "GR": "Greece",
    "HK": "Hong Kong", "HR": "Croatia", "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel",
    "IN": "India", "IS": "Iceland", "IT": "Italy", "JP": "Japan", "KR": "South Korea", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "MD": "Moldova", "MK": "North Macedonia", "MT": "Malta",
    "MX": "Mexico", "MY": "Malaysia", "NG": "Nigeria", "NL": "Netherlands", "NO": "Norway",
    "NZ": "New Zealand", "PE": "Peru", "PH": "Philippines", "PK": "Pakistan", "PL": "Poland",
    "PR": "Puerto Rico", "PT": "Portugal", "RO": "Romania", "RS": "Serbia", "SE": "Sweden",
    "SG": "Singapore", "SI": "Slovenia", "SK": "Slovakia", "TH": "Thailand", "TR": "Turkey",
    "TW": "Taiwan", "UA": "Ukraine", "US": "United States", "VN": "Vietnam", "ZA": "South Africa",
}
# Proton Secure Core entry countries: "CH-US#1" = enter via CH, exit in US.
SECURE_CORE_ENTRIES = {"CH", "IS", "SE"}

INTERFACE_KEYS = {"privatekey", "address", "dns", "listenport", "mtu"}
PEER_KEYS = {"publickey", "presharedkey", "allowedips", "endpoint", "persistentkeepalive"}
HOOK_KEYS = {"preup", "postup", "predown", "postdown"}
CANONICAL = {
    "privatekey": "PrivateKey", "address": "Address", "dns": "DNS", "listenport": "ListenPort",
    "mtu": "MTU", "publickey": "PublicKey", "presharedkey": "PresharedKey", "allowedips": "AllowedIPs",
    "endpoint": "Endpoint", "persistentkeepalive": "PersistentKeepalive",
}

SERVER_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2})(?:-([A-Za-z0-9]{2,6}))?#(\d+)(?:-?(TOR))?", re.I)
FILE_SERVER_RE = re.compile(r"(?<![A-Za-z])([A-Za-z]{2})-([A-Za-z0-9]{2,6})-(\d+)(?![0-9])")


class ConfigError(ValueError):
    pass


# --------------------------------------------------------------------------- key helpers

def decode_key(value: str) -> bytes:
    value = value.strip()
    if len(value) != 44 or not value.endswith("="):
        raise ConfigError("key must be 44 base64 characters")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise ConfigError("key is not valid base64") from None
    if len(raw) != 32:
        raise ConfigError("key must decode to 32 bytes")
    return raw


def x25519_public(private: bytes) -> bytes:
    """Derive the WireGuard public key (RFC 7748 X25519 with base point 9)."""
    p = 2**255 - 19
    k = bytearray(private)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    scalar = int.from_bytes(k, "little")
    x1, x2, z2, x3, z3, swap = 9, 1, 0, 9, 1, 0
    for t in reversed(range(255)):
        bit = (scalar >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % p, (x2 - z2) % p
        aa, bb = a * a % p, b * b % p
        e = (aa - bb) % p
        c, d = (x3 + z3) % p, (x3 - z3) % p
        da, cb = d * a % p, c * b % p
        x3 = (da + cb) ** 2 % p
        z3 = x1 * (da - cb) ** 2 % p
        x2 = aa * bb % p
        z2 = e * (aa + 121665 * e) % p
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, p - 2, p) % p).to_bytes(32, "little")


def key_fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:12]


# --------------------------------------------------------------------------- parsing

@dataclass
class Section:
    kind: str                                   # "interface" | "peer"
    values: dict[str, str] = field(default_factory=dict)
    comments: list[str] = field(default_factory=list)


@dataclass
class WgConfig:
    interface: Section
    peers: list[Section]
    header_comments: list[str] = field(default_factory=list)


def _split_list(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def parse_conf(text: str) -> WgConfig:
    """Strictly parse a WireGuard config. Raises ConfigError on anything odd."""
    if "\x00" in text:
        raise ConfigError("file contains NUL bytes")
    sections: list[Section] = []
    header: list[str] = []
    current: Section | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            comment = line.lstrip("#").strip()
            (current.comments if current else header).append(comment)
            continue
        # wireguard-windows treats everything after '#' as a comment.
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().lower()
            if name not in {"interface", "peer"}:
                raise ConfigError(f"line {lineno}: unknown section [{line[1:-1]}]")
            current = Section(name)
            sections.append(current)
            continue
        if "=" not in line:
            raise ConfigError(f"line {lineno}: expected 'Key = Value'")
        if current is None:
            raise ConfigError(f"line {lineno}: setting outside of a section")
        key, value = (x.strip() for x in line.split("=", 1))
        lk = key.lower()
        if lk in HOOK_KEYS:
            raise ConfigError(f"contains lifecycle command hook '{key}' - rejected for safety")
        allowed = INTERFACE_KEYS if current.kind == "interface" else PEER_KEYS
        if lk not in allowed:
            raise ConfigError(f"line {lineno}: unsupported key '{key}' in [{current.kind.title()}]")
        if lk in current.values and lk not in {"address", "dns", "allowedips"}:
            raise ConfigError(f"line {lineno}: duplicate key '{key}'")
        if lk in current.values:  # list keys may repeat; wireguard concatenates
            current.values[lk] = current.values[lk] + ", " + value
        else:
            current.values[lk] = value

    interfaces = [s for s in sections if s.kind == "interface"]
    peers = [s for s in sections if s.kind == "peer"]
    if len(interfaces) != 1:
        raise ConfigError("config must contain exactly one [Interface] section")
    if not peers:
        raise ConfigError("config must contain at least one [Peer] section")
    cfg = WgConfig(interfaces[0], peers, header)
    _validate(cfg)
    return cfg


def _validate_cidr_list(value: str, what: str) -> None:
    items = _split_list(value)
    if not items:
        raise ConfigError(f"{what} is empty")
    for item in items:
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise ConfigError(f"{what}: '{item}' is not a valid IP/CIDR") from None


def parse_endpoint(value: str) -> tuple[str, int]:
    value = value.strip()
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\]:(\d{1,5})", value) or re.fullmatch(r"([A-Za-z0-9.-]+):(\d{1,5})", value)
    if not m:
        raise ConfigError(f"Endpoint '{value}' must be host:port")
    host, port = m.group(1), int(m.group(2))
    if not 1 <= port <= 65535:
        raise ConfigError(f"Endpoint port {port} out of range")
    return host, port


def _validate(cfg: WgConfig) -> None:
    iv = cfg.interface.values
    if "privatekey" not in iv:
        raise ConfigError("[Interface] PrivateKey is missing")
    try:
        decode_key(iv["privatekey"])
    except ConfigError as e:
        raise ConfigError(f"[Interface] PrivateKey invalid: {e}") from None
    if "address" not in iv:
        raise ConfigError("[Interface] Address is missing")
    _validate_cidr_list(iv["address"], "Address")
    if "dns" in iv:
        for item in _split_list(iv["dns"]):
            try:
                ipaddress.ip_address(item)
            except ValueError:
                if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", item):  # search domain
                    raise ConfigError(f"DNS entry '{item}' is invalid") from None
    for k, lo, hi in (("listenport", 1, 65535), ("mtu", 576, 65535)):
        if k in iv and not (iv[k].isdigit() and lo <= int(iv[k]) <= hi):
            raise ConfigError(f"{CANONICAL[k]} must be an integer {lo}-{hi}")
    for i, peer in enumerate(cfg.peers, 1):
        pv = peer.values
        if "publickey" not in pv:
            raise ConfigError(f"[Peer] #{i} PublicKey is missing")
        for k in ("publickey", "presharedkey"):
            if k in pv:
                try:
                    decode_key(pv[k])
                except ConfigError as e:
                    raise ConfigError(f"[Peer] #{i} {CANONICAL[k]} invalid: {e}") from None
        if "allowedips" not in pv:
            raise ConfigError(f"[Peer] #{i} AllowedIPs is missing")
        _validate_cidr_list(pv["allowedips"], "AllowedIPs")
        if "endpoint" in pv:
            parse_endpoint(pv["endpoint"])
        if "persistentkeepalive" in pv:
            ka = pv["persistentkeepalive"].lower()
            if not (ka == "off" or (ka.isdigit() and 0 <= int(ka) <= 65535)):
                raise ConfigError("PersistentKeepalive must be 0-65535 or off")


# --------------------------------------------------------------------------- metadata

@dataclass
class ServerInfo:
    name: str = ""
    country_code: str = ""
    entry_country_code: str = ""      # secure core entry
    free: bool = False
    tor: bool = False


def detect_server(comments: list[str], filename_stem: str) -> ServerInfo:
    """Find the Proton server name and country from comments or the filename."""
    for c in comments:
        m = SERVER_RE.search(c)
        if m:
            return _server_from_parts(m.group(1), m.group(2), m.group(3), bool(m.group(4)), c.strip())
    m = FILE_SERVER_RE.search(filename_stem)
    if m:
        name = f"{m.group(1).upper()}-{m.group(2).upper()}#{m.group(3)}"
        return _server_from_parts(m.group(1), m.group(2), m.group(3), False, name)
    m = re.search(r"(?<![A-Za-z])([A-Za-z]{2})[-_#]?(\d+)(?![0-9])", filename_stem)
    if m and m.group(1).upper() in COUNTRIES:
        return _server_from_parts(m.group(1), None, m.group(2), False, f"{m.group(1).upper()}#{m.group(2)}")
    for tok in re.findall(r"(?<![A-Za-z])([A-Za-z]{2})(?![A-Za-z])", filename_stem):
        if tok.upper() in COUNTRIES:
            return ServerInfo(name="", country_code=tok.upper())
    return ServerInfo(name=comments[0].strip() if comments else "")


def _server_from_parts(first: str, second: str | None, num: str, tor: bool, name: str) -> ServerInfo:
    first = first.upper()
    second = (second or "").upper()
    info = ServerInfo(name=name, tor=tor or "TOR" in name.upper())
    info.free = second == "FREE"
    if second in COUNTRIES and first in SECURE_CORE_ENTRIES and second != first:
        info.country_code, info.entry_country_code = second, first
    elif first in COUNTRIES:
        info.country_code = first
    return info


@dataclass
class Profile:
    path: Path
    config: WgConfig
    server: ServerInfo
    features: list[str]
    key_name: str = ""

    @property
    def server_name(self) -> str:
        return self.server.name

    @property
    def country_code(self) -> str:
        return self.server.country_code

    @property
    def country(self) -> str:
        return COUNTRIES.get(self.server.country_code, "")

    @property
    def endpoint(self) -> str:
        return self.config.peers[0].values.get("endpoint", "")

    @property
    def dns(self) -> list[str]:
        return _split_list(self.config.interface.values.get("dns", ""))

    @property
    def addresses(self) -> list[str]:
        return _split_list(self.config.interface.values.get("address", ""))

    @property
    def allowed_ips(self) -> list[str]:
        out: list[str] = []
        for p in self.config.peers:
            out += _split_list(p.values.get("allowedips", ""))
        return out

    @property
    def full_tunnel(self) -> bool:
        return has_default_route(self.allowed_ips)

    @property
    def public_key(self) -> str:
        raw = decode_key(self.config.interface.values["privatekey"])
        return base64.b64encode(x25519_public(raw)).decode()

    @property
    def label(self) -> str:
        parts = [self.server_name or self.path.stem]
        if self.server.entry_country_code:
            parts.append(f"Secure Core {self.server.entry_country_code}→{self.country_code}")
        elif self.country:
            parts.append(self.country)
        if self.endpoint:
            parts.append(self.endpoint)
        return " · ".join(parts)


def load_profile(path: Path) -> Profile:
    data = path.read_bytes()
    if len(data) > MAX_CONF_BYTES:
        raise ConfigError(f"file is larger than {MAX_CONF_BYTES // 1024} KB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConfigError("file is not valid UTF-8 text") from None
    cfg = parse_conf(text)
    comments = cfg.peers[0].comments + cfg.header_comments + cfg.interface.comments
    server = detect_server(comments, path.stem)
    features, key_name = [], ""
    for c in cfg.interface.comments:
        m = re.match(r"(?i)key for (.+)", c)
        if m:
            key_name = m.group(1).strip()
        elif "=" in c:
            features.append(re.sub(r"\s*=\s*", " = ", c.strip()))
    return Profile(path=path, config=cfg, server=server, features=features, key_name=key_name)


def list_profiles(vpn_dir: Path) -> tuple[list[Profile], list[tuple[Path, str]]]:
    """Return (valid profiles sorted by filename, [(path, error)])."""
    good, bad = [], []
    if not vpn_dir.exists():
        return good, bad
    for path in sorted(vpn_dir.glob("*.conf"), key=lambda p: p.name.lower()):
        try:
            good.append(load_profile(path))
        except (ConfigError, OSError) as e:
            bad.append((path, str(e)))
    return good, bad


def resolve_profile(token: str | None, profiles: list[Profile], default_name: str = "") -> Profile | None:
    if not profiles:
        return None
    if not token:
        token = default_name
        if not token:
            return profiles[0]
    t = token.lower()
    if t.isdigit():
        idx = int(t) - 1
        return profiles[idx] if 0 <= idx < len(profiles) else None
    for p in profiles:
        if t in (p.path.name.lower(), p.path.stem.lower(), p.server_name.lower()):
            return p
    matches = [p for p in profiles if t in p.path.name.lower() or t in p.server_name.lower()]
    return matches[0] if len(matches) == 1 else None


# --------------------------------------------------------------------------- routing transforms

def has_default_route(allowed: list[str]) -> bool:
    return any(x.replace(" ", "") in ("0.0.0.0/0", "::/0") for x in allowed)


def split_default_routes(value: str) -> str:
    """Balanced mode: /0 -> two /1 halves (avoids wireguard-windows' kill-switch)."""
    out: list[str] = []
    for x in _split_list(value):
        norm = x.replace(" ", "")
        if norm == "0.0.0.0/0":
            new = ["0.0.0.0/1", "128.0.0.0/1"]
        elif norm == "::/0":
            new = ["::/1", "8000::/1"]
        else:
            new = [x]
        for n in new:
            if n not in out:
                out.append(n)
    return ", ".join(out)


def render_runtime_config(cfg: WgConfig, protection: str) -> str:
    """Canonical config text for the tunnel service (no comments, whitelisted keys)."""
    lines = ["[Interface]"]
    order_i = ["privatekey", "address", "dns", "listenport", "mtu"]
    for k in order_i:
        if k in cfg.interface.values:
            lines.append(f"{CANONICAL[k]} = {cfg.interface.values[k]}")
    for peer in cfg.peers:
        lines += ["", "[Peer]"]
        for k in ["publickey", "presharedkey", "allowedips", "endpoint", "persistentkeepalive"]:
            if k not in peer.values:
                continue
            v = peer.values[k]
            if k == "allowedips" and protection == "balanced":
                v = split_default_routes(v)
            lines.append(f"{CANONICAL[k]} = {v}")
    return "\n".join(lines) + "\n"


def tunnel_name(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_=+.-]", "_", path.stem)[:18].strip("._-") or "vpn"
    digest = hashlib.sha256(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:6]
    return f"{TUNNEL_PREFIX}{stem}_{digest}"[:32]


# --------------------------------------------------------------------------- runtime copies

SID_SYSTEM = "*S-1-5-18"
SID_ADMINS = "*S-1-5-32-544"


def harden_dir(path: Path) -> bool:
    """Restrict a directory (and its contents) to SYSTEM + Administrators."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)
        return True
    flags = subprocess.CREATE_NO_WINDOW
    r1 = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                         f"{SID_SYSTEM}:(OI)(CI)F", f"{SID_ADMINS}:(OI)(CI)F"],
                        capture_output=True, text=True, creationflags=flags)
    # Reset children so they inherit the restricted ACL only.
    subprocess.run(["icacls", str(path / "*"), "/reset", "/T", "/C", "/Q"],
                   capture_output=True, text=True, creationflags=flags)
    return r1.returncode == 0


def write_runtime_config(profile: Profile, protection: str, runtime_dir: Path) -> tuple[Path, str]:
    name = tunnel_name(profile.path)
    harden_dir(runtime_dir)
    path = runtime_dir / f"{name}.conf"
    tmp = runtime_dir / f"{name}.conf.tmp"
    tmp.write_text(render_runtime_config(profile.config, protection), encoding="utf-8")
    os.replace(tmp, path)
    return path, name


def remove_runtime_config(runtime_dir: Path, name: str) -> None:
    for p in (runtime_dir / f"{name}.conf", runtime_dir / f"{name}.conf.tmp"):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

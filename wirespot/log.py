"""Optional diagnostic logging with secret redaction.

Logs go to logs\\wirespot-YYYY-MM-DD.log in the data folder. Every
record passes through a redaction filter that removes WireGuard keys and any
secret registered at runtime (the hotspot passphrase) before it is written.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from pathlib import Path

from . import paths

KEEP_DAYS = 14
MAX_BYTES = 5 * 1024 * 1024

_logger = logging.getLogger("wirespot")
_logger.propagate = False
_logger.setLevel(logging.DEBUG)
_secrets: set[str] = set()
_handler: logging.Handler | None = None

_KEY_LINE = re.compile(r"(?i)\b(private\s*key|privatekey|preshared\s*key|presharedkey)(\s*[:=]\s*)(\S+)")
# 32-byte WireGuard keys are 44 base64 chars ending in '='. Public keys are not
# secret, but blanket-redacting key-shaped tokens is cheap insurance.
_B64_KEY = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{43}=(?![A-Za-z0-9+/=])")


def register_secret(value: str) -> None:
    if value and len(value) >= 4:
        _secrets.add(value)


def redact(text: str) -> str:
    if not text:
        return text
    text = _KEY_LINE.sub(lambda m: m.group(1) + m.group(2) + "<redacted>", text)
    text = _B64_KEY.sub("<key>", text)
    for s in sorted(_secrets, key=len, reverse=True):
        text = text.replace(s, "<secret>")
    return text


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        return True


def _cleanup(log_dir: Path) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in log_dir.glob("wirespot-*.log*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def enable(log_dir: Path | None = None) -> Path:
    global _handler
    log_dir = log_dir or paths.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    _cleanup(log_dir)
    path = log_dir / f"wirespot-{date.today().isoformat()}.log"
    if path.exists() and path.stat().st_size > MAX_BYTES:
        rolled = path.with_suffix(".log.1")
        try:
            rolled.unlink(missing_ok=True)
            path.rename(rolled)
        except OSError:
            pass
    if _handler is not None:
        _logger.removeHandler(_handler)
    _handler = logging.FileHandler(path, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S"))
    _handler.addFilter(_RedactFilter())
    _logger.addHandler(_handler)
    _logger.info("---- logging enabled ----")
    return path


def disable() -> None:
    global _handler
    if _handler is not None:
        _logger.info("---- logging disabled ----")
        _logger.removeHandler(_handler)
        _handler.close()
        _handler = None


def enabled() -> bool:
    return _handler is not None


def debug(msg: str) -> None:
    if _handler is not None:
        _logger.debug(msg)


def event(level: str, msg: str) -> None:
    if _handler is not None:
        _logger.log(getattr(logging, level.upper(), logging.INFO), msg)

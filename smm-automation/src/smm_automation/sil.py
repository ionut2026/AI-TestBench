"""Reader for appSMM's SmartInspect logs (``.sil``), for the specifications that check what appSMM logs.

A ``.sil`` file is ``SILF`` followed by packets ``<int16 type><int32 size><body>`` (little endian). Log entries
(type 4) carry the session (appSMM module, e.g. ``BridgeInterface``), a title (the log line) and a timestamp
(OLE automation date, local time). appSMM logs every ICD message it exchanges as
``RX(/is/iw/rx): {"Version":7,"SystemStatusRequest":{}}`` / ``TX(/is/iw/tx): {...}``.

Where the files are comes from ``trace.config`` next to appSMM.exe
(``file(filename=D:\\smm\\logs\\appSMM.sil, rotate=daily, ...)`` -> ``D:\\smm\\logs\\appSMM-<time>.sil``).
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

MAGIC = b"SILF"
LOG_ENTRY = 4
_OLE_EPOCH = datetime(1899, 12, 30)
_ICD_LINE = re.compile(r"^(RX|TX)\((/[^)]*)\):\s*(\{.*\})\s*$", re.S)
_FILE_TARGET = re.compile(r"file\(\s*filename\s*=\s*([^,)]+)", re.I)


@dataclass(frozen=True)
class SilEntry:
    time_ms: float
    session: str
    title: str
    data: bytes = b""


@dataclass(frozen=True)
class LoggedMessage:
    """An ICD message as appSMM logged it; ``way`` is from appSMM's side (``RX`` received, ``TX`` sent)."""

    time_ms: float
    way: str
    topic: str
    name: str
    body: dict
    line: str

    def as_dict(self) -> dict:
        return {"time": self.time_ms, "way": self.way, "topic": self.topic, "name": self.name, "body": self.body, "line": self.line}


def ole_to_epoch_ms(days: float) -> float:
    """SmartInspect timestamps are OLE dates in the local time of the logging PC."""
    return (_OLE_EPOCH + timedelta(days=days)).timestamp() * 1000


def read_entries(path: str | Path) -> Iterator[SilEntry]:
    """Log entries of one file; stops quietly at a packet appSMM is still writing."""
    data = Path(path).read_bytes()
    if data[:4] != MAGIC:
        raise ValueError(f"{path} is not a SmartInspect log (no SILF header)")
    pos = 4
    while pos + 6 <= len(data):
        kind, size = struct.unpack_from("<hi", data, pos)
        if size < 0 or pos + 6 + size > len(data):
            return
        body = data[pos + 6:pos + 6 + size]
        pos += 6 + size
        if kind == LOG_ENTRY and size >= 48:
            yield _log_entry(body)


def _log_entry(body: bytes) -> SilEntry:
    _type, _viewer, app_len, session_len, title_len, host_len, data_len, _pid, _tid = struct.unpack_from("<9i", body, 0)
    (stamp,) = struct.unpack_from("<d", body, 36)
    pos = 48 + app_len
    session = body[pos:pos + session_len].decode("utf-8", "replace")
    pos += session_len
    title = body[pos:pos + title_len].decode("utf-8", "replace")
    pos += title_len + host_len
    return SilEntry(ole_to_epoch_ms(stamp), session, title, body[pos:pos + data_len])


def icd_message(entry: SilEntry) -> LoggedMessage | None:
    """The ICD message in a log line, or None for other lines."""
    m = _ICD_LINE.match(entry.title)
    if not m:
        return None
    try:
        payload = json.loads(m.group(3))
    except ValueError:
        return None
    names = [k for k in payload if k != "Version"] if isinstance(payload, dict) else []
    if len(names) != 1 or not isinstance(payload[names[0]], dict):
        return None
    return LoggedMessage(entry.time_ms, m.group(1), m.group(2), names[0], payload[names[0]], entry.title)


def log_target(appsmm_dir: str | Path) -> Path | None:
    """The appSMM log file configured in ``trace.config`` (``...\\appSMM.sil``), or None."""
    config = Path(appsmm_dir) / "trace.config"
    if not config.is_file():
        return None
    targets = [Path(t.strip().strip('"')) for t in _FILE_TARGET.findall(config.read_text(encoding="utf-8", errors="replace"))]
    preferred = [t for t in targets if t.stem.lower() == "appsmm"]
    return (preferred or targets)[0] if targets else None


def log_files(target: Path, since_ms: float = 0) -> list[Path]:
    """The files of a rotating SmartInspect log (``appSMM.sil``, ``appSMM-<time>.sil``) written since ``since_ms``,
    oldest first."""
    files = [p for p in target.parent.glob(f"{target.stem}*{target.suffix}")
             if p.name == target.name or p.stem.startswith(target.stem + "-")]
    files = [p for p in files if p.stat().st_mtime * 1000 >= since_ms]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def logged_messages(target: Path, since_ms: float = 0, offset_ms: float = 0) -> list[LoggedMessage]:
    """ICD messages appSMM logged at or after ``since_ms``, in log order. ``offset_ms``: how far the log's clock is
    ahead of this PC's (a rig board); the returned times are on this PC's clock."""
    out: list[LoggedMessage] = []
    for path in log_files(target, since_ms):
        for e in read_entries(path):
            if e.time_ms - offset_ms >= since_ms and (m := icd_message(e)):
                out.append(replace(m, time_ms=m.time_ms - offset_ms) if offset_ms else m)
    return out

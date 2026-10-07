import json
import os
import struct
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from smm_automation import sil
from smm_automation.SMMTestbench import SMMTestbench


def _ole(epoch_ms: float) -> float:
    return (datetime.fromtimestamp(epoch_ms / 1000) - datetime(1899, 12, 30)) / timedelta(days=1)


def _log_entry(title: str, epoch_ms: float, session: str = "BridgeInterface") -> bytes:
    app, sess, host, text = b"appSMM", session.encode(), b"PC1", title.encode()
    body = struct.pack("<9i", 100, 0, len(app), len(sess), len(text), len(host), 0, 4242, 1)
    body += struct.pack("<dI", _ole(epoch_ms), 0xFFFFFF00) + app + sess + text + host
    return struct.pack("<hi", sil.LOG_ENTRY, len(body)) + body


def _process_flow(title: str) -> bytes:
    text, host = title.encode(), b"PC1"
    body = struct.pack("<5i", 0, len(text), len(host), 4242, 1) + struct.pack("<d", 46000.0) + text + host
    return struct.pack("<hi", 6, len(body)) + body


def _write(path, *packets: bytes, tail: bytes = b"") -> None:
    path.write_bytes(sil.MAGIC + b"".join(packets) + tail)


NOW = time.time() * 1000


def test_reads_log_entries_skips_other_packets_and_a_half_written_tail(tmp_path):
    f = tmp_path / "appSMM-2026-10-06-13-08-22.sil"
    _write(f, _log_entry("Starting appSMM", NOW, "appSMM"), _process_flow("Bootstrapper.Configure"),
           _log_entry('TX(/is/iw/tx): {"Version":7,"SystemStatusResponse":{"CurrentState":"Idle"}}', NOW + 5),
           tail=_log_entry("cut", NOW)[:20])
    entries = list(sil.read_entries(f))
    assert [e.session for e in entries] == ["appSMM", "BridgeInterface"]
    assert abs(entries[1].time_ms - (NOW + 5)) < 1
    msg = sil.icd_message(entries[1])
    assert (msg.way, msg.topic, msg.name, msg.body) == ("TX", "/is/iw/tx", "SystemStatusResponse", {"CurrentState": "Idle"})
    assert sil.icd_message(entries[0]) is None


def test_rejects_a_file_that_is_not_a_smartinspect_log(tmp_path):
    f = tmp_path / "x.sil"
    f.write_bytes(b"NOPE")
    with pytest.raises(ValueError, match="SILF"):
        list(sil.read_entries(f))


def test_icd_lines_that_are_not_messages_are_ignored():
    for title in ("RX(/is/iw/rx): not json", 'RX(/is/iw/rx): {"Version":7}', 'TX(/is/iw/tx): {"Version":7,"A":{},"B":{}}', "TXT"):
        assert sil.icd_message(sil.SilEntry(0, "BridgeInterface", title)) is None


def test_log_location_from_trace_config_and_rotated_files(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (tmp_path / "trace.config").write_text(
        f'<add initializeData="file(filename={logs}\\canLog.sil, append=true)" />\n'
        f'<add initializeData="file(filename={logs}\\appSMM.sil, append=true, rotate=daily), tcp(host=127.0.0.1)" />\n')
    target = sil.log_target(tmp_path)
    assert target == logs / "appSMM.sil"
    old, new = logs / "appSMM-2026-10-05-12-02-04.sil", logs / "appSMM-2026-10-06-13-08-22.sil"
    _write(old, _log_entry('RX(/is/iw/rx): {"Version":7,"SystemStatusRequest":{}}', NOW - 60_000))
    _write(new, _log_entry('RX(/is/iw/rx): {"Version":7,"SystemStatusRequest":{}}', NOW - 10),
           _log_entry('TX(/is/iw/tx): {"Version":7,"SystemStatusResponse":{"CurrentState":"Idle"}}', NOW))
    _write(logs / "canLog-2026-10-06.sil")
    _write(logs / "appSMMx.sil")
    os.utime(old, (NOW / 1000 - 3600, NOW / 1000 - 3600))
    assert sil.log_files(target) == [old, new]
    assert [m.name for m in sil.logged_messages(target, NOW - 1000)] == ["SystemStatusRequest", "SystemStatusResponse"]
    # a log written by a clock 60 s ahead: the offset brings its entries onto this PC's time line
    shifted = sil.logged_messages(target, NOW - 61_000, offset_ms=60_000)
    assert [m.name for m in shifted] == ["SystemStatusRequest", "SystemStatusResponse"]
    assert abs(shifted[1].time_ms - (NOW - 60_000)) < 1
    assert sil.log_target(tmp_path / "missing") is None


@pytest.fixture
def smm_with_log(tmp_path, monkeypatch):
    log = tmp_path / "appSMM.sil"
    variables = {"${APPSMM_LOG}": str(log)}
    monkeypatch.setattr("smm_automation.SMMTestbench.BuiltIn", lambda: SimpleNamespace(
        get_variable_value=lambda name, default=None: variables.get(name, "PASS")))
    monkeypatch.setattr("time.sleep", lambda s: None)
    lib = SMMTestbench(autostart=False)
    timeline: list[dict] = []
    lib.client = SimpleNamespace(query=lambda flt, limit=1000: [e for e in timeline if e["id"] > flt.get("since", 0)],
                                 mark_all=lambda: (0, 0), environment=lambda: {}, session=lambda: {})
    lib.begin_smm_test()
    return lib, timeline, tmp_path / "appSMM-2026-10-06-13-08-22.sil"


def _iso(epoch_ms: float) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000).astimezone().isoformat()


def test_exchanged_messages_must_be_in_the_appsmm_log(smm_with_log):
    lib, timeline, f = smm_with_log
    t = lib._test_started_ms + 100
    timeline += [
        {"id": 1, "time": _iso(t), "way": "tx", "topic": "/is/iw/rx", "name": "SystemStatusRequest", "body": {}},
        {"id": 2, "time": _iso(t + 20), "way": "rx", "topic": "/is/iw/tx", "name": "SystemStatusResponse", "body": {"CurrentState": "Idle"}},
        {"id": 3, "time": _iso(t + 30), "way": "rx", "topic": "/is/iw/tx", "name": "Other", "body": {}},
    ]
    _write(f, _log_entry('RX(/is/iw/rx): {"Version":7,"SystemStatusRequest":{}}', t - 5),
           _log_entry('TX(/is/iw/tx): {"Version":7,"SystemStatusResponse":{"CurrentState":"Idle"}}', t + 15))
    matched = lib.icd_messages_should_be_logged_by_appsmm("SystemStatusRequest", "SystemStatusResponse")
    assert [m["way"] for m in matched] == ["RX", "TX"]
    assert [m["name"] for m in lib.get_appsmm_log_messages()] == ["SystemStatusRequest", "SystemStatusResponse"]
    assert len(lib.get_appsmm_log_messages("SystemStatusRequest")) == 1


def test_a_message_logged_with_other_content_or_twice_exchanged_once_logged_fails(smm_with_log):
    lib, timeline, f = smm_with_log
    t = lib._test_started_ms + 100
    timeline += [
        {"id": 1, "time": _iso(t), "way": "rx", "topic": "/is/iw/tx", "name": "SystemStatusResponse", "body": {"CurrentState": "Idle"}},
        {"id": 2, "time": _iso(t + 50), "way": "rx", "topic": "/is/iw/tx", "name": "SystemStatusResponse", "body": {"CurrentState": "Idle"}},
    ]
    _write(f, _log_entry(f'TX(/is/iw/tx): {json.dumps({"Version": 7, "SystemStatusResponse": {"CurrentState": "Idle"}})}', t + 5))
    with pytest.raises(AssertionError, match="1 of 2 message"):
        lib.icd_messages_should_be_logged_by_appsmm("SystemStatusResponse", timeout="0s")
    _write(f, _log_entry('TX(/is/iw/tx): {"Version":7,"SystemStatusResponse":{"CurrentState":"E-Stop"}}', t + 5))
    with pytest.raises(AssertionError, match="2 of 2"):
        lib.icd_messages_should_be_logged_by_appsmm("SystemStatusResponse", timeout="0s")
    with pytest.raises(AssertionError, match="No InitializationRequest exchanged"):
        lib.icd_messages_should_be_logged_by_appsmm("InitializationRequest", timeout="0s")
    with pytest.raises(ValueError):
        lib.icd_messages_should_be_logged_by_appsmm()


def test_a_rig_log_is_fetched_and_its_clock_offset_applied(tmp_path, monkeypatch):
    offset = 830_000.0
    variables = {"${OUTPUT DIR}": str(tmp_path)}
    monkeypatch.setattr("smm_automation.SMMTestbench.BuiltIn", lambda: SimpleNamespace(
        get_variable_value=lambda name, default=None: variables.get(name, default)))
    monkeypatch.setattr("time.sleep", lambda s: None)
    lib = SMMTestbench(autostart=False)
    timeline: list[dict] = []
    lib.client = SimpleNamespace(query=lambda flt, limit=1000: [e for e in timeline if e["id"] > flt.get("since", 0)],
                                 mark_all=lambda: (0, 0), environment=lambda: {}, session=lambda: {})
    lib.begin_smm_test()
    t = lib._test_started_ms + 100
    timeline.append({"id": 1, "time": _iso(t), "way": "tx", "topic": "/is/iw/rx", "name": "SystemStatusRequest", "body": {}})
    clock_reads: list[int] = []

    class Control:
        def has(self, what):
            return what in ("fetch-log", "clock")

        def fetch_log(self, dest):
            dest.mkdir(parents=True)
            f = dest / "appSMM-2026-10-06-13-08-22.sil"
            _write(f, _log_entry('RX(/is/iw/rx): {"Version":7,"SystemStatusRequest":{}}', t + offset + 30))
            return [f]

        def log_clock_offset(self):
            clock_reads.append(1)
            return offset, 400.0

    lib._kinds = {"appSmm": "external"}
    lib._rig_control, lib._rig_control_loaded = Control(), True
    matched = lib.icd_messages_should_be_logged_by_appsmm("SystemStatusRequest", timeout="0s")
    assert len(matched) == 1 and abs(matched[0]["time"] - (t + 30)) < 1
    assert [m["name"] for m in lib.get_appsmm_log_messages()] == ["SystemStatusRequest"]
    assert len(clock_reads) == 1 and (tmp_path / "appsmm-log" / "2").is_dir()


def test_log_location_from_the_environment_or_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr("smm_automation.SMMTestbench.BuiltIn", lambda: SimpleNamespace(get_variable_value=lambda *a, **k: None))
    (tmp_path / "trace.config").write_text(f'initializeData="file(filename={tmp_path}\\appSMM.sil, rotate=daily)"')
    lib = SMMTestbench(autostart=False)
    lib.client = SimpleNamespace(environment=lambda: {"appSmm": {"exe": str(tmp_path / "appSMM.exe")}})
    assert lib.get_appsmm_log_location() == str(tmp_path / "appSMM.sil")
    lib.client = SimpleNamespace(environment=lambda: {"appSmm": {"kind": "mock"}})
    assert lib.get_appsmm_log_location() is None
    with pytest.raises(AssertionError, match="unknown"):
        lib.get_appsmm_log_messages()

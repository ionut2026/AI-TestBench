"""HTTP client of the SMM automation service (service/src/api/server.ts, API v1)."""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from smm_automation import FRAMEWORK_ROOT

SUPPORTED_API_MAJOR = 1
DEFAULT_URL = os.environ.get("SMM_AUTOMATION_URL", "http://127.0.0.1:8765/api/v1")
SERVICE_DIR = FRAMEWORK_ROOT / "service"
SERVICE_BUNDLE = SERVICE_DIR / "dist" / "smm-automation-service.mjs"


class ServiceError(Exception):
    """An error answer of the service. kind: timeout | expectation | unavailable | internal | http."""

    def __init__(self, status: int, message: str, kind: str = "http", details: Any = None):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.details = details


class ServiceClient:
    def __init__(self, url: str = DEFAULT_URL, request_margin_s: float = 10.0):
        self.url = url.rstrip("/")
        self.http = requests.Session()
        self.margin = request_margin_s

    # ------------------------------------------------------------------ plumbing

    def call(self, method: str, path: str, body: dict | None = None, wait_s: float = 0.0, **params: Any) -> Any:
        try:
            res = self.http.request(
                method,
                f"{self.url}{path}",
                json=body if method != "GET" else None,
                params={k: v for k, v in params.items() if v is not None} or None,
                timeout=(5, wait_s + self.margin),
            )
        except requests.ConnectionError as err:
            raise ServiceError(0, f"SMM automation service not reachable at {self.url}: {err}", "unreachable") from err
        try:
            data = res.json()
        except ValueError:
            data = {"error": res.text}
        if res.status_code >= 400:
            raise ServiceError(res.status_code, data.get("error", res.reason), data.get("kind", "http"), data.get("details"))
        return data

    def get(self, path: str, **params: Any) -> Any:
        return self.call("GET", path, **params)

    def post(self, path: str, body: dict | None = None, wait_s: float = 0.0) -> Any:
        return self.call("POST", path, body or {}, wait_s)

    # ------------------------------------------------------------------ API

    def health(self) -> dict:
        return self.get("/health")

    def is_up(self) -> bool:
        try:
            self.health()
            return True
        except ServiceError:
            return False

    def start_environment(self, tier: str, overrides: dict | None = None) -> dict:
        return self.post("/environment/start", {"tier": tier, "overrides": overrides or {}}, wait_s=60)

    def stop_environment(self) -> dict:
        return self.post("/environment/stop", wait_s=30)

    def environment(self) -> dict:
        return self.get("/environment")

    def environment_logs(self, since: int = 0, source: str | None = None) -> list[dict]:
        return self.get("/environment/logs", since=since, source=source)

    def restart_appsmm(self, down_ms: int) -> dict:
        return self.post("/environment/restart-appsmm", {"downMs": down_ms}, wait_s=60 + down_ms / 1000)

    def restart_broker(self, down_ms: int) -> dict:
        return self.post("/environment/restart-broker", {"downMs": down_ms}, wait_s=30 + down_ms / 1000)

    def hardware(self) -> dict:
        return self.get("/hardware")

    def hardware_action(self, action: str, args: dict | None = None) -> dict:
        return self.post(f"/hardware/actions/{action}", args)

    def hardware_trace(self, since: int = 0) -> list[dict]:
        """COP commands/responses between appSMM and the hardware twin (``way`` rx = from appSMM)."""
        return self.get("/hardware/trace", since=since)

    def session(self) -> dict:
        return self.get("/session")

    def connect(self, timeout_s: float = 10, clear: bool = True, **settings: Any) -> dict:
        return self.post("/session/connect", {"timeoutMs": int(timeout_s * 1000), "clear": clear, **settings}, wait_s=timeout_s)

    def disconnect(self, abrupt: bool = False) -> dict:
        return self.post("/session/disconnect", {"abrupt": abrupt}, wait_s=10)

    def clear(self) -> None:
        self.post("/session/clear")

    def send(self, name: str, body: Any = None, analyzer: int = -1, strict: bool = True) -> dict:
        return self.post("/messages", {"name": name, "body": {} if body is None else body, "analyzer": analyzer, "strict": strict})

    def send_raw(self, topic: str, raw: str) -> dict:
        return self.post("/messages/raw", {"topic": topic, "raw": raw})

    def validate(self, name: str, body: Any) -> dict:
        return self.post("/messages/validate", {"name": name, "body": body})

    def mark(self) -> int:
        return int(self.post("/timeline/mark")["mark"])

    def mark_all(self) -> tuple[int, int]:
        """(timeline mark, COP trace mark) taken together; the trace mark is 0 without a hardware twin."""
        data = self.post("/timeline/mark")
        return int(data["mark"]), int(data.get("traceMark", 0))

    def trace_wait(self, command: str, since: int, timeout_s: float) -> dict:
        body = {"command": command, "since": since, "timeoutMs": int(timeout_s * 1000)}
        return self.post("/hardware/trace/wait", body, wait_s=timeout_s)

    def trace_expect_none(self, command: str, since: int, duration_s: float) -> None:
        body = {"command": command, "since": since, "durationMs": int(duration_s * 1000)}
        self.post("/hardware/trace/expect-none", body, wait_s=duration_s)

    def query(self, filter: dict | None = None, limit: int = 1000) -> list[dict]:
        return self.post("/timeline/query", {"filter": filter or {}, "limit": limit})

    def wait(self, filter: dict, timeout_s: float) -> dict:
        return self.post("/timeline/wait", {"filter": filter, "timeoutMs": int(timeout_s * 1000)}, wait_s=timeout_s)

    def wait_sequence(self, filters: list[dict], timeout_s: float, since: int | None = None) -> list[dict]:
        body: dict = {"filters": filters, "timeoutMs": int(timeout_s * 1000)}
        if since is not None:
            body["since"] = since
        return self.post("/timeline/sequence", body, wait_s=timeout_s)

    def expect_none(self, filter: dict, duration_s: float) -> None:
        self.post("/timeline/expect-none", {"filter": filter, "durationMs": int(duration_s * 1000)}, wait_s=duration_s)

    def pair_issues(self) -> list[dict]:
        return self.get("/pair-issues")

    def schema(self, name: str) -> dict:
        return self.get(f"/schemas/{name}")

    def icd(self) -> dict:
        return self.get("/icd")


# ---------------------------------------------------------------------- local service process

_spawned: subprocess.Popen | None = None
_spawned_url = DEFAULT_URL


def ensure_service(client: ServiceClient, autostart: bool = True, log_file: Path | None = None, timeout_s: float = 30) -> dict:
    """Returns /health of a running service; starts the bundled service locally if allowed and needed."""
    global _spawned, _spawned_url
    try:
        health = client.health()
    except ServiceError:
        if not autostart:
            raise
        _spawned_url = client.url
        _spawned = _spawn_service(client.url, log_file)
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                health = client.health()
                break
            except ServiceError:
                if _spawned.poll() is not None:
                    raise ServiceError(0, f"The automation service exited with code {_spawned.returncode}; see {log_file or 'its output'}") from None
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
    major = int(str(health.get("apiVersion", "0")).split(".")[0])
    if major != SUPPORTED_API_MAJOR:
        raise ServiceError(0, f"Service API {health.get('apiVersion')} is not supported (need {SUPPORTED_API_MAJOR}.x)")
    return health


def _spawn_service(url: str, log_file: Path | None) -> subprocess.Popen:
    from urllib.parse import urlparse

    if not SERVICE_BUNDLE.exists():
        raise ServiceError(0, f"{SERVICE_BUNDLE} is missing: run 'npm install' and 'npm run build' in {SERVICE_DIR}")
    node = shutil.which("node")
    if not node:
        raise ServiceError(0, "node is not on PATH")
    parsed = urlparse(url)
    out = open(log_file, "a", encoding="utf-8") if log_file else subprocess.DEVNULL  # noqa: SIM115
    proc = subprocess.Popen(
        [node, str(SERVICE_BUNDLE), "--port", str(parsed.port or 8765), "--host", parsed.hostname or "127.0.0.1"],
        cwd=SERVICE_DIR,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    atexit.register(stop_spawned_service)
    return proc


def stop_spawned_service() -> None:
    global _spawned
    proc, _spawned = _spawned, None
    if not proc or proc.poll() is not None:
        return
    try:
        # Lets the service stop appSMM/Mosquitto it started before it exits.
        ServiceClient(_spawned_url).stop_environment()
    except Exception:  # noqa: BLE001
        pass
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()

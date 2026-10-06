"""HTTP client of the SMM automation service (service/src/api/server.ts, API v1)."""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from smm_automation import FRAMEWORK_ROOT

SUPPORTED_API_MAJOR = 1
DEFAULT_URL = os.environ.get("SMM_AUTOMATION_URL", "http://127.0.0.1:8765/api/v1")
SERVICE_DIR = FRAMEWORK_ROOT / "service"
SERVICE_BUNDLE = SERVICE_DIR / "dist" / "smm-automation-service.mjs"
# Where a local service writes its API token (service/src/main.ts): .service/token-<port>.
TOKEN_DIR = FRAMEWORK_ROOT / ".service"
TOKEN_ENV = "SMM_AUTOMATION_TOKEN"


# Parallel runs (smm-auto run --processes N, mock tier only): the worker in slot n gets its own service and embedded
# broker. pabot's PABOTEXECUTIONPOOLID is handed out round-robin and is not exclusive, so a worker claims a slot by
# locking .service/worker-<n>.lock for the life of its robot process (the OS releases it even on a hard kill).
WORKER_PORT_OFFSET = 10
WORKER_SLOTS = 32
MOCK_BROKER_PORT = 1884
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def claim_worker_slot(directory: Path | None = None, slots: int = WORKER_SLOTS) -> tuple[int, Any]:
    """Claims the lowest free worker slot: returns (slot, handle); the slot is held until the handle is closed."""
    directory = directory or TOKEN_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for slot in range(slots):
        handle = open(directory / f"worker-{slot}.lock", "a+b")  # noqa: SIM115 - held for the process lifetime
        try:
            _lock(handle)
        except OSError:
            handle.close()
            continue
        return slot, handle
    raise ServiceError(0, f"No free parallel worker slot (all {slots} locked in {directory})")


def _lock(handle: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def worker_url(url: str, worker: int) -> str:
    """The URL of parallel worker ``worker``'s own local service: the port moved by 10 + worker."""
    parsed = urlparse(url)
    if parsed.hostname not in LOOPBACK:
        raise ServiceError(0, f"Parallel runs need a local automation service, not {url}")
    port = (parsed.port or 8765) + WORKER_PORT_OFFSET + worker
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return parsed._replace(netloc=f"{host}:{port}").geturl()


def worker_broker_port(worker: int) -> int:
    """The embedded MQTT broker port of parallel worker ``worker`` (mock tier)."""
    return MOCK_BROKER_PORT + WORKER_PORT_OFFSET + worker


def token_file(url: str) -> Path:
    return TOKEN_DIR / f"token-{urlparse(url).port or 8765}"


def read_token(url: str) -> str | None:
    """The API token: $SMM_AUTOMATION_TOKEN, else the token file the local service on that port wrote."""
    if os.environ.get(TOKEN_ENV):
        return os.environ[TOKEN_ENV]
    try:
        return token_file(url).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class ServiceError(Exception):
    """An error answer of the service. kind: timeout | expectation | unavailable | unauthorized | internal | http | unreachable."""

    def __init__(self, status: int, message: str, kind: str = "http", details: Any = None):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.details = details


class ServiceClient:
    def __init__(self, url: str = DEFAULT_URL, request_margin_s: float = 10.0, token: str | None = None):
        self.url = url.rstrip("/")
        self.http = requests.Session()
        self.margin = request_margin_s
        self.token = token

    # ------------------------------------------------------------------ plumbing

    def call(self, method: str, path: str, body: dict | None = None, wait_s: float = 0.0, **params: Any) -> Any:
        if self.token is None:
            self.token = read_token(self.url)
        res = self._request(method, path, body, wait_s, params)
        if res.status_code == 401:
            # The service may have been restarted with a new token since it was read: read it again once.
            fresh = read_token(self.url)
            if fresh and fresh != self.token:
                self.token = fresh
                res = self._request(method, path, body, wait_s, params)
        try:
            data = res.json()
        except ValueError:
            data = {"error": res.text}
        if res.status_code == 401:
            raise ServiceError(401, f"{data.get('error', res.reason)}: set ${TOKEN_ENV} or check {token_file(self.url)}", "unauthorized")
        if res.status_code >= 400:
            raise ServiceError(res.status_code, data.get("error", res.reason), data.get("kind", "http"), data.get("details"))
        return data

    def _request(self, method: str, path: str, body: dict | None, wait_s: float, params: dict) -> requests.Response:
        try:
            return self.http.request(
                method,
                f"{self.url}{path}",
                json=body if method != "GET" else None,
                params={k: v for k, v in params.items() if v is not None} or None,
                headers={"Authorization": f"Bearer {self.token}"} if self.token else None,
                timeout=(5, wait_s + self.margin),
            )
        except requests.ConnectionError as err:
            raise ServiceError(0, f"SMM automation service not reachable at {self.url}: {err}", "unreachable") from err

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

    def hardware_faults(self) -> dict:
        return self.get("/hardware/faults")

    def set_hardware_faults(self, faults: list[dict]) -> dict:
        return self.post("/hardware/faults", {"faults": faults})

    def clear_hardware_faults(self) -> dict:
        return self.call("DELETE", "/hardware/faults")

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
_spawned_token: str | None = None


def ensure_service(client: ServiceClient, autostart: bool = True, log_file: Path | None = None, timeout_s: float = 30) -> dict:
    """Returns /health of a running service; starts the bundled service locally if allowed and needed."""
    global _spawned, _spawned_url, _spawned_token
    try:
        health = client.health()
    except ServiceError:
        if not autostart:
            raise
        _spawned_url = client.url
        _spawned_token = os.environ.get(TOKEN_ENV) or secrets.token_hex(24)
        client.token = _spawned_token
        _spawned = _spawn_service(client.url, log_file, _spawned_token)
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


def _spawn_service(url: str, log_file: Path | None, token: str) -> subprocess.Popen:
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
        env={**os.environ, TOKEN_ENV: token},
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
        ServiceClient(_spawned_url, token=_spawned_token).stop_environment()
    except Exception:  # noqa: BLE001
        pass
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    # terminate() gives the service no chance to remove its token file.
    try:
        path = token_file(_spawned_url)
        if path.read_text(encoding="utf-8").strip() == _spawned_token:
            path.unlink()
    except OSError:
        pass

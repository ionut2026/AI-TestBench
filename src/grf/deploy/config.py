"""The site's deployment configuration: a TOML file **outside** the repository (``--config <file>`` or
``<ENV_PREFIX>_DEPLOY_CONFIG``). It names the Jenkins server, the boards (targets) and which component goes to which
board. It never contains a password: a target names the environment variable (``password_env``, set by Jenkins
``withCredentials``) or a file outside the repository (``password_file``) that holds it, or a private key
(``identity``). Example: ``deploy.example.toml`` of a product starter.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from grf.deploy.model import DeployError
from grf.ssh import CLIENTS, HOST_KEY_POLICIES, SshConfig, SshError
from grf.validation import integer, number, table, text

SCHEMA = 1
_FORBIDDEN = {"password", "token", "secret", "passphrase"}


@dataclass(frozen=True)
class JenkinsSettings:
    base_url: str
    ca_bundle: str | None = None
    insecure_tls: bool = False  # explicit opt-in, printed and recorded; never the default
    user_env: str = "JENKINS_USER"
    token_env: str = "JENKINS_TOKEN"
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        number(self.timeout_s, "[jenkins] timeout_s", positive=True)
        for key in ("base_url", "user_env", "token_env"):
            text(getattr(self, key), f"[jenkins] {key}")
        if type(self.insecure_tls) is not bool:
            raise ValueError("[jenkins] insecure_tls must be a boolean")
        if self.ca_bundle is not None:
            text(self.ca_bundle, "[jenkins] ca_bundle")


@dataclass(frozen=True)
class Target(SshConfig):
    workdir: str = "/home/root"

    def __post_init__(self) -> None:
        super().__post_init__()
        text(self.workdir, f"target {self.name} workdir")

    def password(self, env: Mapping[str, str]) -> str | None:
        try:
            return super().password(env)
        except SshError as err:
            raise DeployError(str(err)) from err


@dataclass(frozen=True)
class SiteConfig:
    path: Path
    jenkins: JenkinsSettings | None
    targets: dict[str, Target]
    components: dict[str, str]  # component -> target name
    min_free_mb: int = 1500
    max_load: float = 5.0
    retention: int = 2
    lock_ttl_s: int = 7200
    audit_log: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        integer(self.min_free_mb, "[limits] min_free_mb")
        integer(self.retention, "[limits] retention", minimum=1)
        integer(self.lock_ttl_s, "[limits] lock_ttl_s", minimum=1)
        number(self.max_load, "[limits] max_load", positive=True)
        if self.audit_log is not None:
            text(self.audit_log, "audit_log")

    def target_for(self, component: str, target: str | None = None) -> Target:
        name = target or self.components.get(component)
        if not name:
            raise DeployError(f"no target for component {component!r}: pass --target or set [components] {component} "
                              f"in {self.path}")
        if name not in self.targets:
            raise DeployError(f"unknown target {name!r} ({', '.join(self.targets) or 'none configured'})")
        return self.targets[name]


def config_env(env_prefix: str) -> str:
    return f"{env_prefix}_DEPLOY_CONFIG"


def _inside(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _forbidden_keys(data: Any, where: str = "") -> list[str]:
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in _FORBIDDEN:
                found.append(f"{where}{key}")
            found += _forbidden_keys(value, f"{where}{key}.")
    return found


def parse(data: dict[str, Any], path: Path, project_root: Path | None = None) -> SiteConfig:
    try:
        return _parse(data, path, project_root)
    except ValueError as err:
        raise DeployError(f"{path}: {err}") from err


def _parse(data: dict[str, Any], path: Path, project_root: Path | None = None) -> SiteConfig:
    def fail(message: str) -> DeployError:
        return DeployError(f"{path}: {message}")

    table(data, "deployment config")
    secrets = _forbidden_keys(data)
    if secrets:
        raise fail(f"secrets do not belong in the deployment config ({', '.join(secrets)}); "
                   "use password_env, password_file or identity")
    table(data, "deployment config", {"schema", "jenkins", "targets", "components", "limits", "audit_log"})
    if type(data.get("schema", SCHEMA)) is not int or data.get("schema", SCHEMA) != SCHEMA:
        raise fail(f"schema must be {SCHEMA}")
    jenkins = None
    if "jenkins" in data:
        j = data["jenkins"]
        table(j, "[jenkins]", {f.name for f in fields(JenkinsSettings)})
        if not isinstance(j, dict) or not isinstance(j.get("base_url"), str):
            raise fail("[jenkins] needs base_url")
        url = urlsplit(j["base_url"])
        local = url.hostname in ("localhost", "127.0.0.1")
        if not url.hostname or (url.scheme != "https" and not (url.scheme == "http" and local)):
            raise fail("[jenkins] base_url must use https")
        if url.username is not None or url.password is not None:
            raise fail("[jenkins] credentials do not belong in base_url")
        if j.get("insecure_tls") not in (None, True, False):
            raise fail("[jenkins] insecure_tls must be a boolean")
        jenkins = JenkinsSettings(
            base_url=j["base_url"].rstrip("/"),
            ca_bundle=j.get("ca_bundle"),
            insecure_tls=j.get("insecure_tls", False),
            user_env=j.get("user_env", "JENKINS_USER"),
            token_env=j.get("token_env", "JENKINS_TOKEN"),
            timeout_s=j.get("timeout_s", 60),
        )
    targets = {}
    for name, t in table(data.get("targets", {}), "[targets]").items():
        text(name, "target name")
        table(t, f"[targets.{name}]", {f.name for f in fields(Target)} - {"name"})
        if not isinstance(t, dict):
            raise fail(f"[targets.{name}] must be a table")
        for key in ("host", "user", "known_hosts"):
            if not isinstance(t.get(key), str) or not t[key].strip() or "<" in t[key]:
                raise fail(f"[targets.{name}] {key} must be set (replace the <placeholder>)")
        client = str(t.get("client", "paramiko"))
        if client not in CLIENTS:
            raise fail(f"[targets.{name}] client must be one of {', '.join(CLIENTS)}")
        policy = str(t.get("host_key_policy", "accept-new"))
        if policy not in HOST_KEY_POLICIES:
            raise fail(f"[targets.{name}] host_key_policy must be one of {', '.join(HOST_KEY_POLICIES)}")
        auth = [k for k in ("password_env", "password_file", "identity") if t.get(k)]
        for key in ("password_env", "password_file", "identity"):
            if key in t:
                text(t[key], f"[targets.{name}] {key}")
        if len(auth) > 1 and set(auth) != {"identity"}:
            raise fail(f"[targets.{name}] use only one of password_env and password_file")
        if client == "openssh" and ({"password_env", "password_file"} & set(auth)):
            raise fail(f"[targets.{name}] the openssh client uses keys only; use client = \"paramiko\" for a password")
        for key in ("password_file", "known_hosts", "identity"):
            if t.get(key) and _inside(Path(t[key]), project_root):
                raise fail(f"[targets.{name}] {key} must be outside the repository ({project_root})")
        workdir = str(t.get("workdir", "/home/root")).rstrip("/") or "/"
        if not workdir.startswith("/") or any(c in workdir for c in " ;&|$`'\"\\"):
            raise fail(f"[targets.{name}] workdir must be an absolute board path without spaces or shell characters")
        targets[name] = Target(
            name=name, host=t["host"], user=t["user"], port=t.get("port", 22), client=client,
            password_env=t.get("password_env"), password_file=t.get("password_file"), identity=t.get("identity"),
            known_hosts=t["known_hosts"], host_key_policy=policy, workdir=workdir,
            connect_timeout_s=t.get("connect_timeout_s", 15),
        )
    components = table(data.get("components", {}), "[components]")
    for component, target in components.items():
        text(component, "[components] name")
        text(target, f"[components] {component}")
        if target not in targets:
            raise fail(f"[components] {component} names the unknown target {target!r}")
    limits = table(data.get("limits", {}), "[limits]", {"retention", "min_free_mb", "max_load", "lock_ttl_s"})
    retention = integer(limits.get("retention", 2), "[limits] retention", minimum=1)
    if retention < 1:
        raise fail("[limits] retention must be at least 1")
    audit = data.get("audit_log")
    return SiteConfig(
        path=path, jenkins=jenkins, targets=targets, components=components,
        min_free_mb=limits.get("min_free_mb", 1500), max_load=limits.get("max_load", 5.0),
        retention=retention, lock_ttl_s=limits.get("lock_ttl_s", 7200),
        audit_log=audit,
    )


def load(path: str | Path | None, env_prefix: str, project_root: Path | None = None,
         env: Mapping[str, str] | None = None) -> SiteConfig:
    env = os.environ if env is None else env
    name = config_env(env_prefix)
    path = path or env.get(name)
    if not path:
        raise DeployError(f"no deployment config: pass --config <file> or set {name} (a file outside the repository)")
    file = Path(path)
    try:
        with open(file, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise DeployError(f"cannot read the deployment config {file}: {err}") from err
    return parse(data, file, project_root)

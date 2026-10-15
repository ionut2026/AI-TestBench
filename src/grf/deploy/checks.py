"""``doctor --deploy``: is this machine ready to deploy? Reads the site config, checks credentials are *present*
(never prints them), Jenkins reachability over verified TLS, known host keys and the product's components; with
``--deep`` also logs in to each board and runs ``docker version`` (read-only)."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from grf.deploy.config import SiteConfig, Target, config_env, load
from grf.deploy.jenkins import Jenkins
from grf.deploy.model import Component, DeployError
from grf.deploy.transport import Transport, open_transport
from grf.doctor import Check


def _known(target: Target) -> tuple[str, str]:
    file = Path(target.known_hosts)
    if not file.is_file():
        if target.host_key_policy == "pinned":
            return "FAIL", f"{file} does not exist (host_key_policy = \"pinned\")"
        return "WARN", f"{file} does not exist yet: the first login stores the board's key (accept-new)"
    names = {target.host, f"[{target.host}]:{target.port}"}
    hashed = False
    for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
        hosts = line.split(" ", 1)[0]
        hashed = hashed or hosts.startswith("|1|")
        if names & set(hosts.split(",")):
            return "OK", f"key of {target.host} is in {file}"
    if hashed:
        return "WARN", f"{file} has hashed entries; cannot tell whether {target.host} is known"
    status = "FAIL" if target.host_key_policy == "pinned" else "WARN"
    return status, f"{target.host} is not in {file}" + ("" if status == "FAIL" else " (stored on the first login)")


def deploy_checks(components: list[Component], config: str | None, env_prefix: str, project_root: Path, deep: bool = False,
                  env: Mapping[str, str] | None = None, jenkins_factory: Callable[..., Jenkins] = Jenkins,
                  transport_factory: Callable[[Target, Mapping[str, str]], Transport] = open_transport) -> list[Check]:
    env = os.environ if env is None else env
    checks: list[Check] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append(Check(name, status, detail))

    if components:
        add("deploy: components", "OK", ", ".join(c.name for c in components))
    else:
        add("deploy: components", "WARN", "this product defines no deployable components (ProductPlugin.deploy_components)")
    try:
        site: SiteConfig = load(config, env_prefix, project_root, env)
    except DeployError as err:
        add("deploy: site config", "FAIL", str(err))
        return checks
    add("deploy: site config", "OK", f"{site.path} ({len(site.targets)} target(s))")
    try:
        site.path.resolve().relative_to(project_root.resolve())
        add("deploy: config location", "WARN", f"{site.path} is inside the repository; board addresses belong in a site "
                                               f"file outside it ({config_env(env_prefix)})")
    except ValueError:
        pass
    for component in components:
        try:
            target = site.target_for(component.name)
            add(f"deploy: {component.name} target", "OK", f"{target.name} ({target.user}@{target.host})")
        except DeployError as err:
            add(f"deploy: {component.name} target", "WARN", str(err))
    if site.jenkins is None:
        add("deploy: jenkins", "WARN", "no [jenkins] section: only --local-tar deployments are possible")
    else:
        j = site.jenkins
        if j.ca_bundle and not Path(j.ca_bundle).is_file():
            add("deploy: jenkins CA bundle", "FAIL", f"{j.ca_bundle} not found")
        if j.insecure_tls:
            add("deploy: jenkins TLS", "WARN", "insecure_tls = true: the certificate is not verified")
        user, token = bool(env.get(j.user_env)), bool(env.get(j.token_env))
        if user != token:
            add("deploy: jenkins credentials", "WARN", f"only one of {j.user_env} and {j.token_env} is set: anonymous access is used")
        else:
            add("deploy: jenkins credentials", "OK", f"{j.user_env}/{j.token_env} set" if user else "anonymous read access")
        try:
            jenkins_factory(j, env, retries=1).api(f"{j.base_url}/api/json?tree=mode")
            add("deploy: jenkins", "OK", f"{j.base_url} reachable")
        except DeployError as err:
            add("deploy: jenkins", "FAIL", str(err))
    used = {site.components[c.name] for c in components if c.name in site.components} or set(site.targets)
    for name in sorted(used):
        target = site.targets[name]
        if target.client == "paramiko" and importlib.util.find_spec("paramiko") is None:
            add(f"deploy: {name} ssh client", "FAIL", "paramiko missing: pip install \"general-robot-framework[deploy]\"")
        if target.password_env:
            ok = bool(env.get(target.password_env))
            add(f"deploy: {name} credential", "OK" if ok else "FAIL",
                f"{target.password_env} {'is set' if ok else 'is not set (Jenkins withCredentials sets it)'}")
        elif target.password_file:
            ok = Path(target.password_file).is_file()
            add(f"deploy: {name} credential", "OK" if ok else "FAIL", f"password_file {'found' if ok else 'not found'}")
        elif target.identity:
            ok = Path(target.identity).is_file()
            add(f"deploy: {name} credential", "OK" if ok else "FAIL", f"identity {'found' if ok else 'not found'}")
        else:
            add(f"deploy: {name} credential", "WARN", "no password_env, password_file or identity")
        add(f"deploy: {name} host key", *_known(target))
        if deep:
            try:
                transport = transport_factory(target, env)
                try:
                    version = transport.run("docker version --format '{{.Server.Version}}'").stdout.strip()
                finally:
                    transport.close()
                add(f"deploy: {name} board", "OK", f"ssh {target.user}@{target.host} OK, docker {version}")
            except DeployError as err:
                add(f"deploy: {name} board", "FAIL", str(err))
    return checks

"""Jenkins client: resolves a build of a component's job (number, ``lastSuccessfulBuild``, branch), finds its one
matching artifact, downloads it with size and SHA-256 and records where it came from.

TLS is verified (``[jenkins] ca_bundle`` for a site CA); ``insecure_tls = true`` is an explicit, recorded opt-in.
Redirects must stay on the configured host. Credentials (optional, anonymous read as in EST) come from the
environment variables ``JENKINS_USER``/``JENKINS_TOKEN`` (Jenkins ``withCredentials``) and are never printed.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.client
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from grf.deploy.config import JenkinsSettings
from grf.deploy.model import ArtifactRef, Component, DeployError, Outcome

_BUILD = re.compile(r"^(\d+|lastSuccessfulBuild|lastStableBuild|lastBuild)$")


def job_url(settings: JenkinsSettings, component: Component, branch: str | None = None) -> str:
    """Exact configured job identity; branch names are encoded as one Jenkins path segment."""
    url = f"{settings.base_url}/{component.jenkins_job.strip('/')}"
    if branch is not None and (not isinstance(branch, str) or not branch.strip()):
        raise DeployError("Jenkins branch must be nonempty", Outcome.ARTIFACT_FAILED)
    return f"{url}/job/{quote(branch, safe='')}" if branch is not None else url


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).hostname != self.host:
            raise DeployError(f"Jenkins redirected to another host ({urlsplit(newurl).hostname}); refused",
                              Outcome.ARTIFACT_FAILED)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def ssl_context(settings: JenkinsSettings) -> ssl.SSLContext:
    if settings.insecure_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return ssl.create_default_context(cafile=settings.ca_bundle) if settings.ca_bundle else ssl.create_default_context()


class Jenkins:
    def __init__(self, settings: JenkinsSettings, env: Mapping[str, str] | None = None,
                 opener: urllib.request.OpenerDirector | None = None, sleep: Callable[[float], None] = time.sleep,
                 retries: int = 3):
        self.settings = settings
        env = os.environ if env is None else env
        user, token = env.get(settings.user_env), env.get(settings.token_env)
        self._auth = ("Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()) if user and token else None
        self.authenticated = self._auth is not None
        host = urlsplit(settings.base_url).hostname or ""
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl_context(settings)), _SameHostRedirect(host))
        self.sleep, self.retries = sleep, retries

    # ------------------------------------------------------------------ urls

    def job_url(self, component: Component, branch: str | None = None) -> str:
        return job_url(self.settings, component, branch)

    def _build_job(self, component: Component, branch: str | None) -> str:
        job = self.job_url(component, branch)
        if branch is None:
            data = self.api(f"{job}/api/json?tree=_class,jobs[name]")
            kind = data.get("_class")
            if not isinstance(kind, str) or not kind.strip():
                raise DeployError(f"cannot establish Jenkins job type for {component.name}; configure its branch "
                                  "or grant read access to job metadata", Outcome.ARTIFACT_FAILED)
            if "jobs" in data or "multibranch" in kind.lower() or "folder" in kind.lower():
                raise DeployError(f"{component.name} is a multibranch/container job; configure [deploy.branches] "
                                  f"{component.name} or --branch {component.name}=BRANCH; parent job refused",
                                  Outcome.ARTIFACT_FAILED)
        return job

    def _open(self, url: str, timeout_s: float | None = None):
        request = urllib.request.Request(url, headers={"Accept": "application/json, */*"})
        if self._auth:
            request.add_header("Authorization", self._auth)
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return self.opener.open(request, timeout=timeout_s or self.settings.timeout_s)
            except urllib.error.HTTPError as err:
                if err.code < 500:
                    raise DeployError(f"Jenkins answered {err.code} {err.reason} for {url}", Outcome.ARTIFACT_FAILED) from None
                last = err
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                reason = getattr(err, "reason", err)
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise DeployError(f"TLS certificate of {urlsplit(url).hostname} not trusted ({reason.verify_message}); "
                                      "set [jenkins] ca_bundle to the site CA", Outcome.ARTIFACT_FAILED) from None
                last = err
            self.sleep(2 ** attempt)
        raise DeployError(f"Jenkins not reachable at {url}: {last}", Outcome.ARTIFACT_FAILED)

    def api(self, url: str) -> dict[str, Any]:
        with self._open(url) as response:
            try:
                return json.loads(response.read().decode("utf-8"))
            except ValueError as err:
                raise DeployError(f"Jenkins returned no JSON for {url}", Outcome.ARTIFACT_FAILED) from err

    # ------------------------------------------------------------------ queries

    def branches(self, component: Component) -> list[str]:
        data = self.api(f"{self.job_url(component)}/api/json?tree=jobs[name]")
        return sorted(str(j["name"]) for j in data.get("jobs", []))

    def builds(self, component: Component, branch: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        job = self._build_job(component, branch)
        data = self.api(f"{job}/api/json?tree=builds[number,result,timestamp,url]{{0,{int(limit)}}}")
        return [{"number": b.get("number"), "result": b.get("result"), "timestamp": _iso(b.get("timestamp")),
                 "url": b.get("url")} for b in data.get("builds", [])]

    def resolve(self, component: Component, build: str = "lastSuccessfulBuild", branch: str | None = None) -> ArtifactRef:
        if not _BUILD.match(build):
            raise DeployError(f"build must be a number or lastSuccessfulBuild, not {build!r}", Outcome.ARTIFACT_FAILED)
        job = self._build_job(component, branch)
        tree = "number,result,timestamp,url,artifacts[fileName,relativePath],actions[lastBuiltRevision[SHA1]]"
        data = self.api(f"{job}/{build}/api/json?tree={tree}")
        if data.get("result") != "SUCCESS":
            raise DeployError(f"{component.name} build {data.get('number', build)} result is {data.get('result')}, "
                              "only SUCCESS builds are deployed", Outcome.ARTIFACT_FAILED)
        matches = [a for a in data.get("artifacts", []) if component.matches(str(a.get("fileName", "")))]
        if len(matches) != 1:
            names = ", ".join(a.get("fileName", "?") for a in data.get("artifacts", [])) or "none"
            raise DeployError(f"{component.name} build {data.get('number')}: {len(matches)} artifacts match "
                              f"{component.artifact_pattern!r} (exactly one needed; artifacts: {names})",
                              Outcome.ARTIFACT_FAILED)
        artifact = matches[0]
        number = int(data["number"])
        build_url = str(data.get("url") or f"{job}/{number}/")
        revision = next((a["lastBuiltRevision"].get("SHA1") for a in data.get("actions", [])
                         if isinstance(a, dict) and isinstance(a.get("lastBuiltRevision"), dict)), None)
        name = str(artifact["fileName"])
        return ArtifactRef(
            component=component.name, source="jenkins", file_name=name, version=component.version_of(name),
            url=f"{job}/{number}/artifact/{quote(str(artifact['relativePath']))}", job_url=job, branch=branch,
            build_number=number, build_url=build_url, build_result="SUCCESS", build_timestamp=_iso(data.get("timestamp")),
            revision=revision,
        )

    def download(self, ref: ArtifactRef, dest_dir: Path, chunk: int = 1 << 20) -> ArtifactRef:
        if not ref.url:
            raise DeployError("artifact has no URL", Outcome.ARTIFACT_FAILED)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / ref.file_name
        partial = target.with_name(target.name + ".partial")
        digest, size = hashlib.sha256(), 0
        expected: str | None = None
        try:
            with self._open(ref.url, timeout_s=max(self.settings.timeout_s, 600)) as response, partial.open("wb") as out:
                expected = response.headers.get("Content-Length")
                while True:
                    block = response.read(chunk)
                    if not block:
                        break
                    digest.update(block)
                    size += len(block)
                    out.write(block)
        except (http.client.IncompleteRead, OSError) as err:
            partial.unlink(missing_ok=True)
            raise DeployError(f"download of {ref.file_name} failed after {size} bytes: {err}", Outcome.ARTIFACT_FAILED) from None
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        if expected is not None and int(expected) != size:
            partial.unlink(missing_ok=True)
            raise DeployError(f"download of {ref.file_name} incomplete ({size} of {expected} bytes)", Outcome.ARTIFACT_FAILED)
        if size == 0:
            partial.unlink(missing_ok=True)
            raise DeployError(f"{ref.file_name} is empty", Outcome.ARTIFACT_FAILED)
        partial.replace(target)
        ref.path, ref.size, ref.sha256 = str(target), size, digest.hexdigest()
        return ref


def sha256_file(path: Path, chunk: int = 1 << 20) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as f:
        while block := f.read(chunk):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def local_artifact(component: Component, path: Path) -> ArtifactRef:
    if not path.is_file():
        raise DeployError(f"local tarball {path} does not exist", Outcome.ARTIFACT_FAILED)
    if not component.matches(path.name):
        raise DeployError(f"{path.name} does not match the {component.name} artifact pattern {component.artifact_pattern!r}",
                          Outcome.ARTIFACT_FAILED)
    sha, size = sha256_file(path)
    if size == 0:
        raise DeployError(f"{path} is empty", Outcome.ARTIFACT_FAILED)
    return ArtifactRef(component=component.name, source="local", file_name=path.name, version=component.version_of(path.name),
                       size=size, sha256=sha, path=str(path.resolve()))


def _iso(ms: Any) -> str | None:
    if not isinstance(ms, int | float):
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).isoformat(timespec="seconds")

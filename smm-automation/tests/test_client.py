import pytest

from smm_automation import client as client_mod
from smm_automation.client import ServiceClient, ServiceError, read_token, token_file

URL = "http://127.0.0.1:8799/api/v1"


class FakeResponse:
    def __init__(self, status: int, data: dict):
        self.status_code = status
        self._data = data
        self.reason = "reason"
        self.text = ""

    def json(self):
        return self._data


class FakeHttp:
    """Answers 200 to the expected token, 401 to anything else; records the Authorization headers."""

    def __init__(self, accepted: str):
        self.accepted = accepted
        self.seen: list[str | None] = []

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        auth = (headers or {}).get("Authorization")
        self.seen.append(auth)
        if auth == f"Bearer {self.accepted}":
            return FakeResponse(200, {"ok": True})
        return FakeResponse(401, {"error": "Missing or wrong API token", "kind": "unauthorized"})


@pytest.fixture
def token_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(client_mod, "TOKEN_DIR", tmp_path)
    monkeypatch.delenv("SMM_AUTOMATION_TOKEN", raising=False)
    return tmp_path


def _client(http: FakeHttp, token: str | None = None) -> ServiceClient:
    c = ServiceClient(URL, token=token)
    c.http = http  # type: ignore[assignment]
    return c


def test_token_comes_from_the_environment_before_the_token_file(token_dir, monkeypatch):
    assert token_file(URL) == token_dir / "token-8799"
    assert read_token(URL) is None
    (token_dir / "token-8799").write_text("from-file\n", encoding="utf-8")
    assert read_token(URL) == "from-file"
    monkeypatch.setenv("SMM_AUTOMATION_TOKEN", "from-env")
    assert read_token(URL) == "from-env"


def test_requests_carry_the_token_of_the_local_service(token_dir):
    (token_dir / "token-8799").write_text("abc", encoding="utf-8")
    http = FakeHttp("abc")
    assert _client(http).environment() == {"ok": True}
    assert http.seen == ["Bearer abc"]


def test_a_restarted_service_with_a_new_token_is_picked_up(token_dir):
    (token_dir / "token-8799").write_text("new", encoding="utf-8")
    http = FakeHttp("new")
    assert _client(http, token="old").environment() == {"ok": True}
    assert http.seen == ["Bearer old", "Bearer new"]


def test_a_wrong_token_is_an_unauthorized_error_that_names_the_token_file(token_dir):
    http = FakeHttp("right")
    with pytest.raises(ServiceError) as err:
        _client(http, token="wrong").environment()
    assert err.value.kind == "unauthorized" and err.value.status == 401
    assert "SMM_AUTOMATION_TOKEN" in str(err.value) and "token-8799" in str(err.value)
    assert http.seen == ["Bearer wrong"]

"""Ll2HttpFetcher のテスト（ローカル HTTP スタブを使う）。"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from launch_feed.errors import RateLimitedError, UpstreamError
from launch_feed.ll2_client import USER_AGENT, Ll2HttpFetcher


class Stub:
    """スタブサーバの設定と受信記録。"""

    def __init__(self) -> None:
        self.status = 200
        self.body: bytes = json.dumps({"count": 0, "next": None, "results": []}).encode()
        self.headers: dict[str, str] = {}
        self.delay = 0.0
        self.requests: list[dict[str, Any]] = []
        self.base_url = ""


@pytest.fixture
def stub() -> Iterator[Stub]:
    state = Stub()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state.requests.append({"path": self.path, "headers": dict(self.headers)})
            if state.delay:
                time.sleep(state.delay)
            try:
                self.send_response(state.status)
                for k, v in state.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(state.body)))
                self.end_headers()
                self.wfile.write(state.body)
            except OSError:
                pass

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_success_and_query(stub: Stub) -> None:
    stub.body = json.dumps({"count": 1, "next": None, "results": [{"id": "a"}]}).encode()
    page = Ll2HttpFetcher(stub.base_url + "/", None).fetch_upcoming_page(offset=100)
    assert page["results"] == [{"id": "a"}]
    assert len(stub.requests) == 1
    split = urlsplit(stub.requests[0]["path"])
    assert split.path == "/launches/upcoming/"
    qs = parse_qs(split.query)
    assert qs == {
        "mode": ["detailed"],
        "limit": ["100"],
        "ordering": ["net"],
        "offset": ["100"],
    }
    headers = {k.lower(): v for k, v in stub.requests[0]["headers"].items()}
    assert headers["user-agent"] == USER_AGENT
    assert headers["accept"] == "application/json"
    assert "authorization" not in headers


def test_base_url_without_trailing_slash(stub: Stub) -> None:
    Ll2HttpFetcher(stub.base_url, None).fetch_upcoming_page(offset=0)
    assert stub.requests[0]["path"].startswith("/launches/upcoming/?")


def test_authorization_header_only_with_api_key(stub: Stub) -> None:
    Ll2HttpFetcher(stub.base_url, "secret-key-123").fetch_upcoming_page(offset=0)
    headers = {k.lower(): v for k, v in stub.requests[0]["headers"].items()}
    assert headers["authorization"] == "Token secret-key-123"


def test_429_with_retry_after(stub: Stub) -> None:
    stub.status = 429
    stub.headers = {"Retry-After": "120"}
    with pytest.raises(RateLimitedError) as info:
        Ll2HttpFetcher(stub.base_url, None).fetch_upcoming_page(offset=0)
    assert info.value.retry_after_seconds == 120
    assert len(stub.requests) == 1


def test_429_without_retry_after(stub: Stub) -> None:
    stub.status = 429
    with pytest.raises(RateLimitedError) as info:
        Ll2HttpFetcher(stub.base_url, None).fetch_upcoming_page(offset=0)
    assert info.value.retry_after_seconds is None


def test_429_with_http_date_retry_after(stub: Stub) -> None:
    stub.status = 429
    stub.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    with pytest.raises(RateLimitedError) as info:
        Ll2HttpFetcher(stub.base_url, None).fetch_upcoming_page(offset=0)
    assert info.value.retry_after_seconds is None


@pytest.mark.parametrize("status", [500, 503, 404])
def test_error_statuses_are_upstream_errors(stub: Stub, status: int) -> None:
    stub.status = status
    with pytest.raises(UpstreamError, match=str(status)):
        Ll2HttpFetcher(stub.base_url, None).fetch_upcoming_page(offset=0)
    assert len(stub.requests) == 1


def test_non_json_body(stub: Stub) -> None:
    stub.body = b"<html>not json</html>"
    with pytest.raises(UpstreamError):
        Ll2HttpFetcher(stub.base_url, None).fetch_upcoming_page(offset=0)


def test_json_array_body(stub: Stub) -> None:
    stub.body = b"[1, 2, 3]"
    with pytest.raises(UpstreamError):
        Ll2HttpFetcher(stub.base_url, None).fetch_upcoming_page(offset=0)


def test_timeout(stub: Stub) -> None:
    stub.delay = 1.0
    with pytest.raises(UpstreamError, match="timed out"):
        Ll2HttpFetcher(stub.base_url, None, timeout_s=0.2).fetch_upcoming_page(offset=0)


def test_connection_refused() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(UpstreamError):
        Ll2HttpFetcher(f"http://127.0.0.1:{port}", None, timeout_s=2).fetch_upcoming_page(
            offset=0
        )


def test_api_key_not_in_exception_message(stub: Stub) -> None:
    key = "super-secret-key-xyz"
    stub.status = 500
    with pytest.raises(UpstreamError) as info:
        Ll2HttpFetcher(stub.base_url, key).fetch_upcoming_page(offset=0)
    assert key not in str(info.value)
    stub.status = 429
    with pytest.raises(RateLimitedError) as info2:
        Ll2HttpFetcher(stub.base_url, key).fetch_upcoming_page(offset=0)
    assert key not in str(info2.value)

"""Launch Library 2 への HTTP 取得（Fetcher 実装）と取得エラーの分類。"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from launch_feed.errors import RateLimitedError, UpstreamError

logger = logging.getLogger(__name__)

USER_AGENT = "space-launch-feed (+https://github.com/Kam-IF/space-launch-feed)"
PAGE_SIZE = 100
MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # detailed 1 ページ（100 件）の実測は約 2.7MB


class Fetcher(Protocol):
    """upcoming の 1 ページを取得するもの。"""

    def fetch_upcoming_page(self, *, offset: int) -> Mapping[str, Any]:
        """GET {base}/launches/upcoming/?mode=detailed&limit=100&ordering=net&offset={offset}

        戻り値は LL2 のページ（count / next / results）。
        """
        ...


def _parse_retry_after(value: str | None) -> int | None:
    """Retry-After が整数秒ならその値、それ以外（日付形式・不正値）は None。"""
    if value is None:
        return None
    text = value.strip()
    if text.isascii() and text.isdigit():
        return int(text)
    return None


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (socket.timeout, TimeoutError))


class Ll2HttpFetcher:
    """urllib による LL2 取得。1 呼び出し 1 リクエストで、リトライしない。"""

    def __init__(self, base_url: str, api_key: str | None, timeout_s: float = 30.0) -> None:
        """base_url: LL2 のベース URL。api_key: 任意。timeout_s: 秒。"""
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or None
        self._timeout_s = timeout_s

    def _build_url(self, offset: int) -> str:
        return (
            f"{self._base_url}/launches/upcoming/"
            f"?mode=detailed&limit={PAGE_SIZE}&ordering=net&offset={offset}"
        )

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        return headers

    def fetch_upcoming_page(self, *, offset: int) -> Mapping[str, Any]:
        """upcoming の 1 ページを取得する。

        Raises:
            RateLimitedError: HTTP 429。
            UpstreamError: タイムアウト、接続失敗、5xx、その他の非 2xx、JSON でない応答、
                応答サイズが MAX_RESPONSE_BYTES 超過。
        """
        url = self._build_url(offset)
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        if self._api_key:
            # リダイレクト先（別ホスト）へ資格情報を引き継がない
            request.add_unredirected_header("Authorization", f"Token {self._api_key}")
        logger.info("LL2 へリクエスト: offset=%d", offset)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
                raise RateLimitedError(
                    f"LL2 returned HTTP 429 (retry_after={retry_after})",
                    retry_after_seconds=retry_after,
                ) from None
            raise UpstreamError(f"LL2 returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            if _is_timeout(exc.reason):
                raise UpstreamError(f"LL2 request timed out after {self._timeout_s}s") from None
            raise UpstreamError(
                f"LL2 connection failed: {type(exc.reason).__name__}"
            ) from None
        except (socket.timeout, TimeoutError):
            raise UpstreamError(f"LL2 request timed out after {self._timeout_s}s") from None
        except (OSError, http.client.HTTPException) as exc:
            raise UpstreamError(f"LL2 connection failed: {type(exc).__name__}") from None

        if len(body) > MAX_RESPONSE_BYTES:
            raise UpstreamError(f"LL2 response exceeds {MAX_RESPONSE_BYTES} bytes")
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise UpstreamError("LL2 response is not valid JSON") from None
        if not isinstance(data, dict):
            raise UpstreamError(
                f"LL2 response is not a JSON object (got {type(data).__name__})"
            )
        return data

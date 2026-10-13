"""フィード生成で共有する例外。CLI はこれらを終了コードに対応づける。"""

from __future__ import annotations


class FeedError(Exception):
    """フィード生成に関する例外の基底クラス。"""


class RateLimitedError(FeedError):
    """LL2 が HTTP 429 を返した（終了コード 3）。"""

    def __init__(self, message: str = "rate limited", retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class UpstreamError(FeedError):
    """タイムアウト、接続失敗、5xx、その他の非 2xx、JSON でない応答（終了コード 4）。"""


class ValidationFailedError(FeedError):
    """フィードがスキーマに適合しない（終了コード 5）。"""


class EmptyFeedError(FeedError):
    """有効な打ち上げが 0 件（終了コード 6）。"""


class ConfigError(FeedError):
    """設定ファイル・引数の不正（終了コード 2）。"""

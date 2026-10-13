"""例外階層と FakeFetcher のテスト。"""

from __future__ import annotations

import pytest

from conftest import FakeFetcher
from launch_feed.errors import (
    ConfigError,
    EmptyFeedError,
    FeedError,
    RateLimitedError,
    UpstreamError,
    ValidationFailedError,
)


@pytest.mark.parametrize(
    "cls", [RateLimitedError, UpstreamError, ValidationFailedError, EmptyFeedError, ConfigError]
)
def test_errors_derive_from_feed_error(cls: type[Exception]) -> None:
    assert issubclass(cls, FeedError)


def test_rate_limited_keeps_retry_after() -> None:
    assert RateLimitedError(retry_after_seconds=30).retry_after_seconds == 30
    assert RateLimitedError().retry_after_seconds is None


def test_fake_fetcher_returns_pages_in_order() -> None:
    pages = [{"results": [1]}, {"results": [2]}, {"results": [3]}]
    fetcher = FakeFetcher(pages)
    assert [fetcher.fetch_upcoming_page(offset=o) for o in (0, 100, 200)] == pages
    assert fetcher.call_count == 3
    assert fetcher.offsets == [0, 100, 200]


def test_fake_fetcher_raises_on_specified_page() -> None:
    fetcher = FakeFetcher([{"a": 1}, {"a": 2}], errors={1: RateLimitedError()})
    assert fetcher.fetch_upcoming_page(offset=0) == {"a": 1}
    with pytest.raises(RateLimitedError):
        fetcher.fetch_upcoming_page(offset=100)
    assert fetcher.call_count == 2


def test_load_fixture(load_fixture) -> None:  # type: ignore[no-untyped-def]
    data = load_fixture("ll2_edge_cases.json")
    assert data["results"]
    assert all("_case" in item for item in data["results"])

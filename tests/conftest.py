"""テスト共通の fixture と FakeFetcher。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PAGE_SIZE = 100


class FakeFetcher:
    """Fetcher プロトコルのテスト用実装。offset // 100 番目のページを返す。"""

    def __init__(
        self,
        pages: Sequence[Mapping[str, Any]],
        errors: Mapping[int, Exception] | None = None,
    ) -> None:
        """pages: ページ本体の列。errors: ページ番号（0 始まり）→ 投げる例外。"""
        self.pages = list(pages)
        self.errors = dict(errors or {})
        self.offsets: list[int] = []

    @property
    def call_count(self) -> int:
        """fetch_upcoming_page の呼び出し回数。"""
        return len(self.offsets)

    def fetch_upcoming_page(self, *, offset: int) -> Mapping[str, Any]:
        """呼び出しを記録し、指定ページの例外か本体を返す。"""
        self.offsets.append(offset)
        index = offset // PAGE_SIZE
        if index in self.errors:
            raise self.errors[index]
        return self.pages[index]


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """tests/fixtures/ 配下の JSON を読む関数を返す。例: load_fixture("ll2_edge_cases.json")。"""

    def _load(relative_path: str) -> Any:
        return json.loads((FIXTURES_DIR / relative_path).read_text(encoding="utf-8"))

    return _load

"""実行設定（エンドポイント・API キー・設定ファイルの解決）とローカル入力 Fetcher。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from launch_feed.errors import ConfigError, UpstreamError

ENDPOINTS: dict[str, str] = {
    "prod": "https://ll.thespacedevs.com/2.3.0",
    "dev": "https://lldev.thespacedevs.com/2.3.0",
}
SCHEMA_RELATIVE = Path("schema") / "launches.v1.schema.json"
OVERRIDES_RELATIVE = Path("config") / "country_overrides.json"


@dataclass(frozen=True)
class Settings:
    """解決済みの実行設定。api_key は repr に出さない。"""

    base_url: str
    api_key: str | None = field(repr=False)
    schema_path: Path
    overrides_path: Path


def resolve_settings(
    *,
    endpoint: str = "prod",
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """引数と環境変数から設定を解決する。LL2_BASE_URL は endpoint より優先。

    schema / config が存在しなければ ConfigError。
    """
    env = os.environ if env is None else env
    if endpoint not in ENDPOINTS:
        raise ConfigError(f"unknown endpoint: {endpoint!r}")
    base_url = (env.get("LL2_BASE_URL") or "").strip() or ENDPOINTS[endpoint]
    api_key = (env.get("LL2_API_KEY") or "").strip() or None
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    schema_path = root / SCHEMA_RELATIVE
    overrides_path = root / OVERRIDES_RELATIVE
    for path in (schema_path, overrides_path):
        if not path.is_file():
            raise ConfigError(f"required file not found: {path}")
    return Settings(base_url, api_key, schema_path, overrides_path)


class DirectoryFetcher:
    """ネットワークを使わず <dir>/upcoming_p{N}.json を順にページとして返す Fetcher。"""

    def __init__(self, input_dir: Path, page_size: int = 100) -> None:
        """input_dir: ページ JSON のあるディレクトリ（無ければ ConfigError）。"""
        self._dir = Path(input_dir)
        self._page_size = page_size
        if not self._dir.is_dir():
            raise ConfigError(f"input directory not found: {self._dir}")

    def fetch_upcoming_page(self, *, offset: int) -> Mapping[str, Any]:
        """offset // page_size + 1 番目のファイルを読む。無ければ UpstreamError。"""
        path = self._dir / f"upcoming_p{offset // self._page_size + 1}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise UpstreamError(f"input page not found: {path}") from None
        except (OSError, ValueError) as exc:
            raise UpstreamError(f"cannot read input page {path}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise UpstreamError(f"input page is not an object: {path}")
        return data

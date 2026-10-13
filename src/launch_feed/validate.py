"""フィードの JSON Schema 検証と 0 件チェック。"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from launch_feed.errors import ConfigError, EmptyFeedError, ValidationFailedError

logger = logging.getLogger(__name__)

MAX_REPORTED_ERRORS = 5


@lru_cache(maxsize=None)
def _load_validator(schema_path: Path) -> Draft202012Validator:
    """スキーマを読み込んで検証器を作る（path ごとにキャッシュ）。"""
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read schema {schema_path}: {exc}") from exc
    except ValueError as exc:  # UnicodeDecodeError / JSONDecodeError を含む
        raise ConfigError(f"schema {schema_path} is not valid JSON: {exc}") from exc
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ConfigError(f"schema {schema_path} is not a valid JSON Schema: {exc.message}") from exc
    return Draft202012Validator(schema)


def _pointer(error: ValidationError) -> str:
    """エラー位置を JSON Pointer 風の文字列にする（ルートは "/"）。"""
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in error.absolute_path]
    return "/" + "/".join(parts)


def validate_feed(feed: Mapping[str, Any], schema_path: Path) -> None:
    """フィードを検証する。

    launches が空配列なら EmptyFeedError（スキーマ検証より前に判定）。
    スキーマに適合しなければ ValidationFailedError（先頭最大 5 件と総件数を含む）。
    スキーマの読込失敗（ファイルなし・JSON 不正・スキーマ不正）は ConfigError。
    """
    launches = feed.get("launches")
    if isinstance(launches, list) and len(launches) == 0:
        raise EmptyFeedError("launches is empty")

    validator = _load_validator(Path(schema_path).resolve())
    found = sorted(
        ((_pointer(e), e.message) for e in validator.iter_errors(feed)),
        key=lambda item: item,
    )
    if not found:
        return

    total = len(found)
    shown = found[:MAX_REPORTED_ERRORS]
    lines = [f"{path}: {message}" for path, message in shown]
    message = f"feed does not match schema ({total} error(s)); " + "; ".join(lines)
    if total > len(shown):
        message += f"; ... and {total - len(shown)} more"
    logger.error("schema validation failed: %d error(s)", total)
    raise ValidationFailedError(message)

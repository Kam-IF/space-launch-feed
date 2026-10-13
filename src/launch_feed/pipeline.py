"""取得 → 変換 → 検証 → 出力のパイプライン。検証が通るまでファイルを書かない。"""

from __future__ import annotations

import html
import json
import logging
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from launch_feed.countries import CountryOverrides
from launch_feed.errors import ConfigError, UpstreamError
from launch_feed.ll2_client import Fetcher
from launch_feed.transform import TransformResult, format_utc, transform_launches
from launch_feed.validate import validate_feed

logger = logging.getLogger(__name__)

SOURCE_NAME = "The Space Devs – Launch Library 2"
SOURCE_URL = "https://thespacedevs.com/llapi"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FetchResult:
    """取得結果。部分結果は作らない（失敗時は例外）。"""

    pages_fetched: int
    total_count: int | None
    truncated: bool
    results: list[Any]


@dataclass(frozen=True)
class RunSummary:
    """実行結果の要約。"""

    fetched: int
    deduplicated: int
    skipped: int
    empty_countries: int
    output: int
    pages_fetched: int
    total_count: int | None
    truncated: bool
    output_path: Path


def fetch_all(fetcher: Fetcher, *, page_size: int = 100, max_pages: int = 3) -> FetchResult:
    """offset 0 から page_size ずつ、next が無くなるか max_pages まで取得する。

    1 ページでも失敗したら例外をそのまま上げる。results が配列でなければ UpstreamError。
    offset ページングのため、取得中にデータが更新されると取りこぼし・重複が起こりうる
    （重複は id で排除。取りこぼしは仕様上の許容事項で、次回の実行で解消する）。
    """
    results: list[Any] = []
    total: int | None = None
    pages = 0
    more = False
    while pages < max_pages:
        page = fetcher.fetch_upcoming_page(offset=pages * page_size)
        page_results = page.get("results") if isinstance(page, Mapping) else None
        if not isinstance(page_results, list):
            raise UpstreamError(f"page {pages + 1}: 'results' is not an array")
        results.extend(page_results)
        pages += 1
        count = page.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            total = count
        more = bool(page.get("next"))
        if not more:
            break
    truncated = more
    if truncated:
        logger.warning(
            "fetch truncated at %d page(s): LL2 count=%s, fetched %d launches",
            pages,
            total,
            len(results),
        )
    logger.info("fetched %d page(s), %d launches (LL2 count=%s)", pages, len(results), total)
    return FetchResult(pages_fetched=pages, total_count=total, truncated=truncated, results=results)


def _dedupe(raw: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    out: list[Any] = []
    for item in raw:
        key = item.get("id") if isinstance(item, Mapping) else None
        if isinstance(key, str):
            if key in seen:
                continue
            seen.add(key)
        out.append(item)
    return out


def build_feed(
    fetch_result: FetchResult, overrides: CountryOverrides, *, generated_at: datetime
) -> tuple[dict[str, Any], TransformResult]:
    """id 重複排除（先勝ち）→ 変換 → 変換後の net で安定ソート → ルートを組み立てる。"""
    unique = _dedupe(fetch_result.results)
    transformed = transform_launches(unique, overrides)
    ordered = sorted(transformed.launches, key=lambda launch: str(launch["net"]))
    generated = format_utc(generated_at.astimezone(UTC).isoformat())
    feed: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated,
        "source": {"name": SOURCE_NAME, "url": SOURCE_URL},
        "launches": ordered,
    }
    return feed, TransformResult(launches=ordered, skipped=transformed.skipped)


def _index_html() -> str:
    name = html.escape(SOURCE_NAME)
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>LaunchClock launch feed</title>\n</head>\n<body>\n"
        "<h1>LaunchClock launch feed</h1>\n"
        "<p>A JSON feed of upcoming rocket launches, regenerated periodically.</p>\n"
        '<p><a href="v1/launches.json">v1/launches.json</a></p>\n'
        f'<p>Source: <a href="{html.escape(SOURCE_URL)}">{name}</a></p>\n'
        "</body>\n</html>\n"
    )


_GENERATED_FILES = {"index.html", "v1"}


def _is_previous_output(out_dir: Path) -> bool:
    """out_dir が空、または前回の生成物（index.html と v1/launches.json）だけか。"""
    if out_dir.is_symlink() or not out_dir.is_dir():
        return False
    entries = {p.name: p for p in out_dir.iterdir()}
    if not set(entries) <= _GENERATED_FILES:
        return False
    index = entries.get("index.html")
    if index is not None and (index.is_symlink() or not index.is_file()):
        return False
    v1 = entries.get("v1")
    if v1 is not None:
        if v1.is_symlink() or not v1.is_dir():
            return False
        children = list(v1.iterdir())
        if any(c.name != "launches.json" or c.is_symlink() or not c.is_file() for c in children):
            return False
    return True


def write_output(feed: Mapping[str, Any], out_dir: Path) -> Path:
    """一時ディレクトリに全部書いてから out_dir へ入れ替える。launches.json のパスを返す。

    既存の out_dir が空でも前回の生成物だけでもなければ ConfigError（何も消さない）。"""
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        if not _is_previous_output(out_dir):
            raise ConfigError(
                f"output directory {out_dir} exists and is not empty or a previous feed output; "
                "refusing to overwrite it"
            )
    parent = out_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=parent))
    try:
        (tmp / "v1").mkdir()
        (tmp / "v1" / "launches.json").write_text(
            json.dumps(feed, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        (tmp / "index.html").write_text(_index_html(), encoding="utf-8")
        tmp.chmod(0o755)
        if out_dir.exists():
            shutil.rmtree(out_dir)
        tmp.rename(out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out_dir / "v1" / "launches.json"


def run(
    fetcher: Fetcher,
    *,
    overrides: CountryOverrides,
    schema_path: Path,
    out_dir: Path,
    generated_at: datetime | None = None,
    max_pages: int = 3,
) -> RunSummary:
    """fetch_all → build_feed → validate_feed → write_output。"""
    fetched = fetch_all(fetcher, max_pages=max_pages)
    feed, transformed = build_feed(
        fetched, overrides, generated_at=generated_at or datetime.now(UTC)
    )
    validate_feed(feed, schema_path)
    path = write_output(feed, out_dir)
    launches = feed["launches"]
    empty = sum(
        1
        for launch in launches
        if not any(launch["countries"].get(role) for role in ("launchSite", "launchProvider", "payloadOperator"))
    )
    summary = RunSummary(
        fetched=len(fetched.results),
        deduplicated=len(_dedupe(fetched.results)),
        skipped=len(transformed.skipped),
        empty_countries=empty,
        output=len(launches),
        pages_fetched=fetched.pages_fetched,
        total_count=fetched.total_count,
        truncated=fetched.truncated,
        output_path=path,
    )
    logger.info(
        "built %d launches (skipped %d, empty countries %d)", summary.output, summary.skipped, empty
    )
    return summary

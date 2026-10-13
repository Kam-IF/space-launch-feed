"""パイプライン（正常系）のテスト。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from conftest import FIXTURES_DIR, FakeFetcher

from launch_feed.countries import CountryOverrides
from launch_feed.pipeline import run
from launch_feed.validate import validate_feed

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "launches.v1.schema.json"
FIXED = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _pages() -> list[dict]:
    return [
        json.loads((FIXTURES_DIR / "run" / f"upcoming_p{n}.json").read_text(encoding="utf-8"))
        for n in (1, 2)
    ]


def test_run_writes_valid_sorted_feed(tmp_path: Path) -> None:
    fetcher = FakeFetcher(_pages())
    out = tmp_path / "dist"
    summary = run(
        fetcher,
        overrides=CountryOverrides.empty(),
        schema_path=SCHEMA,
        out_dir=out,
        generated_at=FIXED,
    )
    path = out / "v1" / "launches.json"
    assert summary.output_path == path
    feed = json.loads(path.read_text(encoding="utf-8"))
    validate_feed(feed, SCHEMA)
    assert list(feed) == ["schemaVersion", "generatedAt", "source", "launches"]
    assert feed["generatedAt"] == "2026-01-01T00:00:00Z"
    nets = [launch["net"] for launch in feed["launches"]]
    assert nets == sorted(nets)
    ids = [launch["id"] for launch in feed["launches"]]
    assert len(ids) == len(set(ids)) == summary.output
    assert (out / "index.html").is_file()
    assert 'lang="en"' in (out / "index.html").read_text(encoding="utf-8")
    assert fetcher.call_count == 2
    assert fetcher.offsets == [0, 100]
    assert summary.pages_fetched == 2
    assert not summary.truncated

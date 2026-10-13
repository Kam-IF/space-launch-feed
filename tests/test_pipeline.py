"""パイプライン（正常系）のテスト。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import FIXTURES_DIR, FakeFetcher

from launch_feed.countries import CountryOverrides
from launch_feed import __main__ as cli
from launch_feed import pipeline
from launch_feed.errors import (
    EmptyFeedError,
    RateLimitedError,
    UpstreamError,
    ValidationFailedError,
)
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


# ---- 異常系 ----

REPO = Path(__file__).resolve().parent.parent


def _run(fetcher: FakeFetcher, out: Path, **kwargs):
    return run(
        fetcher,
        overrides=CountryOverrides.empty(),
        schema_path=SCHEMA,
        out_dir=out,
        generated_at=FIXED,
        **kwargs,
    )


def _cli(monkeypatch: pytest.MonkeyPatch, fetcher: FakeFetcher, out: Path) -> int:
    """CLI の main を、取得層だけ FakeFetcher に差し替えて呼ぶ。"""
    monkeypatch.setattr(cli, "Ll2HttpFetcher", lambda *args, **kwargs: fetcher)
    return cli.main(["build", "--out", str(out), "--repo-root", str(REPO)])


def _siblings(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir())


def _edge_missing_page() -> dict:
    raw = json.loads((FIXTURES_DIR / "ll2_edge_cases.json").read_text(encoding="utf-8"))
    return {"count": 4, "next": None, "results": raw["results"][:3] + [raw["results"][3]]}


def _fake_page(n: int, count: int = 0, *, more: bool = True) -> dict:
    results = [
        {
            "id": f"10000000-0000-0000-0000-{n:04d}{i:08d}",
            "name": f"Fake {n}-{i}",
            "net": f"2026-12-{n:02d}T0{i}:00:00Z",
        }
        for i in range(2)
    ]
    return {"count": count, "next": "https://example.invalid/next" if more else None, "results": results}


def test_rate_limited_on_second_page(tmp_path: Path, monkeypatch, capsys) -> None:
    pages = _pages()
    err = RateLimitedError(retry_after_seconds=60)
    out = tmp_path / "dist"
    with pytest.raises(RateLimitedError) as info:
        _run(FakeFetcher(pages, errors={1: err}), out)
    assert info.value.retry_after_seconds == 60
    assert not out.exists()
    assert _siblings(out) == []

    assert _cli(monkeypatch, FakeFetcher(pages, errors={1: err}), out) == 3
    assert "60" in capsys.readouterr().err
    assert not out.exists()


def test_upstream_error_on_first_page(tmp_path: Path, monkeypatch) -> None:
    pages = _pages()
    out = tmp_path / "dist"
    fetcher = FakeFetcher(pages, errors={0: UpstreamError("boom")})
    with pytest.raises(UpstreamError):
        _run(fetcher, out)
    assert fetcher.call_count == 1
    assert not out.exists()
    assert _cli(monkeypatch, FakeFetcher(pages, errors={0: UpstreamError("boom")}), out) == 4
    assert not out.exists()


def test_all_launches_invalid_is_empty_feed(tmp_path: Path, monkeypatch) -> None:
    page = _edge_missing_page()
    assert len(page["results"]) == 4
    out = tmp_path / "dist"
    with pytest.raises(EmptyFeedError):
        _run(FakeFetcher([page]), out)
    assert not out.exists()
    assert _cli(monkeypatch, FakeFetcher([page]), out) == 6
    assert not out.exists()


def test_schema_violation_is_validation_failed(tmp_path: Path, monkeypatch) -> None:
    real = pipeline.transform_launches

    def bad_transform(raw, overrides):
        result = real(raw, overrides)
        for launch in result.launches:
            launch["unexpectedKey"] = 1
        return result

    monkeypatch.setattr(pipeline, "transform_launches", bad_transform)
    out = tmp_path / "dist"
    with pytest.raises(ValidationFailedError):
        _run(FakeFetcher(_pages()), out)
    assert not out.exists()
    assert _cli(monkeypatch, FakeFetcher(_pages()), out) == 5
    assert not out.exists()


def test_truncates_at_three_pages_with_warning(tmp_path: Path, caplog) -> None:
    pages = [_fake_page(n, count=999) for n in (1, 2, 3, 4)]
    fetcher = FakeFetcher(pages)
    out = tmp_path / "dist"
    with caplog.at_level(logging.WARNING):
        summary = _run(fetcher, out)
    assert fetcher.offsets == [0, 100, 200]
    assert summary.truncated
    assert summary.pages_fetched == 3
    assert summary.output == 6
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("truncated" in m and "999" in m for m in warnings)


def test_duplicate_id_across_page_boundary(tmp_path: Path) -> None:
    p1, p2 = _pages()
    baseline = _run(FakeFetcher([p1, p2]), tmp_path / "base")
    p2_dup = {**p2, "results": [p1["results"][-1], *p2["results"]]}
    out = tmp_path / "dist"
    summary = _run(FakeFetcher([p1, p2_dup]), out)
    assert summary.fetched == baseline.fetched + 1
    assert summary.output == baseline.output
    feed = json.loads((out / "v1" / "launches.json").read_text(encoding="utf-8"))
    ids = [launch["id"] for launch in feed["launches"]]
    assert len(ids) == len(set(ids)) == baseline.output


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_existing_out_dir_untouched_on_failure_replaced_on_success(tmp_path: Path) -> None:
    out = tmp_path / "dist"
    (out / "v1").mkdir(parents=True)
    (out / "v1" / "launches.json").write_text('{"old":true}', encoding="utf-8")
    (out / "index.html").write_text("old", encoding="utf-8")
    before = _snapshot(out)

    with pytest.raises(UpstreamError):
        _run(FakeFetcher(_pages(), errors={1: UpstreamError("x")}), out)
    with pytest.raises(EmptyFeedError):
        _run(FakeFetcher([_edge_missing_page()]), out)
    assert _snapshot(out) == before
    assert _siblings(out) == ["dist"]

    _run(FakeFetcher(_pages()), out)
    after = _snapshot(out)
    assert after != before
    assert "schemaVersion" in after[str(Path("v1") / "launches.json")].decode()
    assert _siblings(out) == ["dist"]


def test_write_failure_leaves_no_out_dir_or_temp(tmp_path: Path, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline.json, "dumps", boom)
    out = tmp_path / "work" / "dist"
    with pytest.raises(OSError):
        _run(FakeFetcher(_pages()), out)
    assert not out.exists()
    assert _siblings(out) == []

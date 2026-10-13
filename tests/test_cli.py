"""CLI と設定のテスト。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES_DIR

from launch_feed import pipeline
from launch_feed.__main__ import main
from launch_feed.errors import ConfigError, UpstreamError
from launch_feed.settings import DirectoryFetcher, resolve_settings

REPO = Path(__file__).resolve().parent.parent
RUN_DIR = FIXTURES_DIR / "run"


def test_build_from_input_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(REPO)
    code = main(["build", "--out", str(tmp_path / "dist"), "--input-dir", str(RUN_DIR)])
    assert code == 0
    assert (tmp_path / "dist" / "v1" / "launches.json").is_file()
    assert (tmp_path / "dist" / "index.html").is_file()
    assert "output:" in capsys.readouterr().out


def test_missing_schema_is_exit_2(tmp_path: Path, capsys) -> None:
    out = tmp_path / "dist"
    code = main(["build", "--out", str(out), "--input-dir", str(RUN_DIR), "--repo-root", str(tmp_path)])
    assert code == 2
    assert not out.exists()
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_missing_input_dir_is_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)
    code = main(["build", "--out", str(tmp_path / "dist"), "--input-dir", str(tmp_path / "nope")])
    assert code == 2


def test_bad_args_exit_2() -> None:
    with pytest.raises(SystemExit) as info:
        main(["build"])
    assert info.value.code == 2


def test_base_url_env_overrides_endpoint() -> None:
    s = resolve_settings(endpoint="dev", repo_root=REPO, env={"LL2_BASE_URL": "http://x/y"})
    assert s.base_url == "http://x/y"
    assert resolve_settings(endpoint="dev", repo_root=REPO, env={}).base_url.startswith(
        "https://lldev."
    )
    assert resolve_settings(repo_root=REPO, env={}).base_url.startswith("https://ll.")


def test_empty_api_key_is_unset() -> None:
    assert resolve_settings(repo_root=REPO, env={"LL2_API_KEY": ""}).api_key is None
    s = resolve_settings(repo_root=REPO, env={"LL2_API_KEY": "secret"})
    assert s.api_key == "secret"
    assert "secret" not in repr(s)


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        resolve_settings(repo_root=tmp_path, env={})


def test_directory_fetcher_missing_page() -> None:
    fetcher = DirectoryFetcher(RUN_DIR)
    assert fetcher.fetch_upcoming_page(offset=0)["results"]
    with pytest.raises(UpstreamError):
        fetcher.fetch_upcoming_page(offset=200)


def test_broken_schema_is_exit_2(tmp_path: Path, capsys) -> None:
    out = tmp_path / "dist"
    repo = tmp_path / "repo"
    shutil.copytree(REPO / "config", repo / "config")
    (repo / "schema").mkdir()
    (repo / "schema" / "launches.v1.schema.json").write_text("{broken", encoding="utf-8")
    code = main(["build", "--out", str(out), "--input-dir", str(RUN_DIR), "--repo-root", str(repo)])
    assert code == 2
    assert not out.exists()
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_unexpected_error_is_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, caplog
) -> None:
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.chdir(REPO)
    monkeypatch.setattr(pipeline.json, "dumps", boom)
    out = tmp_path / "dist"
    code = main(["build", "--out", str(out), "--input-dir", str(RUN_DIR)])
    assert code == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert "unexpected error" in err and "disk full" in err
    assert len(err.strip().splitlines()) == 1 or "Traceback" in err
    assert any(r.exc_info for r in caplog.records)  # スタックトレースはログに残る

"""起動の間隔のガードのテスト。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from launch_feed.__main__ import main
from launch_feed.run_guard import RunInfo, build_ran, decide, recent_runs

NOW = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
TEN_MIN = timedelta(minutes=10)
API = "https://api.github.com"
REPO = "Kam-IF/space-launch-feed"


def _run(run_id: int, minutes_ago: float, built: bool = True) -> RunInfo:
    return RunInfo(run_id=run_id, created_at=NOW - timedelta(minutes=minutes_ago), built=built)


def _decide(*runs: RunInfo, current: int = 99):
    return decide(runs, current_run_id=current, now=NOW, min_interval=TEN_MIN)


def test_builds_when_no_other_runs() -> None:
    assert _decide().build


def test_skips_when_another_run_built_within_interval() -> None:
    decision = _decide(_run(1, 3))
    assert not decision.build
    assert "run 1" in decision.reason


def test_builds_when_last_build_is_older_than_interval() -> None:
    assert _decide(_run(1, 10.5)).build


def test_ignores_runs_that_did_not_build() -> None:
    assert _decide(_run(1, 2, built=False), _run(2, 5, built=False)).build


def test_ignores_the_current_run() -> None:
    assert _decide(_run(99, 0)).build


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (None, False),
        ({"status": "queued", "conclusion": None}, False),
        ({"status": "waiting", "conclusion": None}, False),
        ({"status": "in_progress", "conclusion": None}, True),
        ({"status": "completed", "conclusion": "success"}, True),
        ({"status": "completed", "conclusion": "failure"}, True),
        ({"status": "completed", "conclusion": "skipped"}, False),
        ({"status": "completed", "conclusion": "cancelled"}, False),
    ],
)
def test_build_ran(job: Mapping[str, Any] | None, expected: bool) -> None:
    assert build_ran(job) is expected


def _fake_github(runs: list[dict[str, Any]], jobs: dict[int, list[dict[str, Any]]]):
    calls: list[str] = []

    def get(url: str) -> Mapping[str, Any]:
        calls.append(url)
        if url.startswith(f"{API}/repos/{REPO}/actions/workflows/publish-feed.yml/runs"):
            return {"workflow_runs": runs}
        for run_id, run_jobs in jobs.items():
            if url.startswith(f"{API}/repos/{REPO}/actions/runs/{run_id}/jobs"):
                return {"jobs": run_jobs}
        raise AssertionError(f"unexpected url: {url}")

    return get, calls


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_recent_runs_reads_only_runs_in_the_window() -> None:
    get, calls = _fake_github(
        runs=[
            {"id": 99, "created_at": _iso(0)},
            {"id": 3, "created_at": _iso(4)},
            {"id": 2, "created_at": _iso(30)},
        ],
        jobs={3: [{"name": "guard", "status": "completed", "conclusion": "success"},
                  {"name": "build", "status": "completed", "conclusion": "success"}]},
    )
    runs = recent_runs(
        get, api_url=API, repo=REPO, workflow="publish-feed.yml",
        current_run_id=99, now=NOW, min_interval=TEN_MIN,
    )
    assert runs == [RunInfo(run_id=3, created_at=NOW - timedelta(minutes=4), built=True)]
    # 窓の外（30 分前）と自分自身のジョブは取得しない
    assert not any("/runs/2/" in c or "/runs/99/" in c for c in calls)


def _guard_args(tmp_path: Path) -> list[str]:
    return ["guard", "--repo", REPO, "--workflow", "publish-feed.yml", "--run-id", "99"]


def test_cli_writes_false_when_recent_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    get, _ = _fake_github(
        runs=[{"id": 3, "created_at": _iso(4)}],
        jobs={3: [{"name": "build", "status": "completed", "conclusion": "success"}]},
    )
    code = main(_guard_args(tmp_path), github_get=get, now=NOW)
    assert code == 0
    assert out.read_text() == "build=false\n"
    assert "build=false" in capsys.readouterr().out


def test_cli_writes_true_when_no_recent_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    get, _ = _fake_github(
        runs=[{"id": 3, "created_at": _iso(4)}],
        jobs={3: [{"name": "build", "status": "completed", "conclusion": "skipped"}]},
    )
    assert main(_guard_args(tmp_path), github_get=get, now=NOW) == 0
    assert out.read_text() == "build=true\n"


def test_cli_skips_when_history_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    def failing(url: str) -> Mapping[str, Any]:
        raise OSError("HTTP 403")

    assert main(_guard_args(tmp_path), github_get=failing, now=NOW) == 0
    assert out.read_text() == "build=false\n"
    assert "::warning title=run guard failed::" in capsys.readouterr().err


def test_cli_skips_without_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(_guard_args(tmp_path), now=NOW) == 0
    assert out.read_text() == "build=false\n"

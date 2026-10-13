"""起動の間隔のガード。前回の生成から一定時間たっていなければ、今回の生成を飛ばす。

外部の定期起動（cron-job.org からの workflow_dispatch）の設定ミスや不正な呼び出しで
起動が大量に来ても、Launch Library 2 へのリクエストが無料枠（1 時間 15 回）を超えないようにする。

判定は GitHub Actions の実行履歴で行う。同じワークフローの他の実行のうち、作成が
``min_interval`` 以内で、``build`` ジョブが動いた・動いている（スキップ・取り消し・未開始以外の）
ものがあれば飛ばす。待機中でまだ ``build`` が始まっていない実行は数えない（その実行は後で自分で判定する）。
ワークフローは concurrency で 1 本ずつ動くため、同時に起動されても後の実行は前の実行の
完了後に判定し、前の実行を見て飛ばす。

履歴を取得できないときは**飛ばす側に倒す**（大量の起動で API が失敗しているときに
生成を続けると、Launch Library 2 へのリクエストが増え続けるため）。次の起動で再び判定する。
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

BUILD_JOB_NAME = "build"

#: ``build`` ジョブがこの結果なら、生成は行われていない（リクエストを使っていない）。
_NOT_BUILT = frozenset({"skipped", "cancelled"})


def build_ran(job: Mapping[str, Any] | None) -> bool:
    """``build`` ジョブが動いた、または動いているか。"""
    if job is None:
        return False
    status = job.get("status")
    if status == "in_progress":
        return True
    return status == "completed" and job.get("conclusion") not in _NOT_BUILT


@dataclass(frozen=True)
class RunInfo:
    """判定に使う実行の情報。"""

    run_id: int
    created_at: datetime
    #: ``build`` ジョブが動いた・動いているか（:func:`build_ran`）。
    built: bool


@dataclass(frozen=True)
class Decision:
    """判定の結果。"""

    build: bool
    reason: str


def decide(
    runs: Iterable[RunInfo],
    *,
    current_run_id: int,
    now: datetime,
    min_interval: timedelta,
) -> Decision:
    """他の実行の履歴から、今回 ``build`` を行うかを決める。"""
    cutoff = now - min_interval
    for run in sorted(runs, key=lambda r: r.created_at, reverse=True):
        if run.run_id == current_run_id or run.created_at < cutoff:
            continue
        if not run.built:
            continue
        age = int((now - run.created_at).total_seconds())
        return Decision(
            build=False,
            reason=f"run {run.run_id} built {age}s ago (< {int(min_interval.total_seconds())}s)",
        )
    return Decision(build=True, reason="no build within the interval")


JsonGetter = Callable[[str], Mapping[str, Any]]


def github_json_getter(token: str, *, timeout_s: float = 15.0) -> JsonGetter:
    """GitHub REST API の GET を返す。"""

    def get(url: str) -> Mapping[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "space-launch-feed-run-guard",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))

    return get


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def recent_runs(
    get: JsonGetter,
    *,
    api_url: str,
    repo: str,
    workflow: str,
    current_run_id: int,
    now: datetime,
    min_interval: timedelta,
) -> list[RunInfo]:
    """判定の窓に入る他の実行と、その ``build`` ジョブの結果を取得する。"""
    base = f"{api_url.rstrip('/')}/repos/{repo}/actions"
    listing = get(f"{base}/workflows/{workflow}/runs?per_page=30")
    cutoff = now - min_interval
    result: list[RunInfo] = []
    for raw in listing.get("workflow_runs", []):
        run_id = int(raw["id"])
        created_at = _parse_time(str(raw["created_at"]))
        if run_id == current_run_id or created_at < cutoff:
            continue
        jobs = get(f"{base}/runs/{run_id}/jobs?per_page=100").get("jobs", [])
        build_job = next((job for job in jobs if job.get("name") == BUILD_JOB_NAME), None)
        result.append(RunInfo(run_id=run_id, created_at=created_at, built=build_ran(build_job)))
    return result

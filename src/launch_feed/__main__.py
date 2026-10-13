"""CLI.

- ``python -m launch_feed build --out <dir> [--endpoint prod|dev] [--input-dir <dir>]``
- ``python -m launch_feed guard --repo <owner/name> --workflow <file> --run-id <id> [--min-interval <seconds>]``
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from launch_feed.countries import CountryOverrides
from launch_feed.errors import (
    ConfigError,
    EmptyFeedError,
    FeedError,
    RateLimitedError,
    UpstreamError,
    ValidationFailedError,
)
from launch_feed.ll2_client import Fetcher, Ll2HttpFetcher
from launch_feed.pipeline import RunSummary, run
from launch_feed.run_guard import JsonGetter, decide, github_json_getter, recent_runs
from launch_feed.settings import DirectoryFetcher, resolve_settings

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_CONFIG = 2
EXIT_RATE_LIMITED = 3
EXIT_UPSTREAM = 4
EXIT_VALIDATION = 5
EXIT_EMPTY = 6


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_CONFIG, f"{self.prog}: error: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="launch_feed")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build the launch feed")
    build.add_argument("--out", required=True, type=Path, help="output directory")
    build.add_argument("--endpoint", choices=["prod", "dev"], default="prod")
    build.add_argument("--input-dir", type=Path, help="read upcoming_pN.json instead of the network")
    build.add_argument("--repo-root", type=Path, help="repository root (default: cwd)")
    guard = sub.add_parser("guard", help="decide whether to build (skip if another run built recently)")
    guard.add_argument("--repo", required=True, help="owner/name")
    guard.add_argument("--workflow", required=True, help="workflow file name, e.g. publish-feed.yml")
    guard.add_argument("--run-id", required=True, type=int, help="id of the current run")
    guard.add_argument("--min-interval", type=int, default=600, help="seconds (default: 600)")
    return parser


def _print_summary(s: RunSummary) -> None:
    print(f"pages fetched: {s.pages_fetched} (LL2 count: {s.total_count}, truncated: {s.truncated})")
    print(f"launches: fetched {s.fetched}, after dedup {s.deduplicated}, skipped {s.skipped}")
    print(f"launches with no related country: {s.empty_countries}")
    print(f"output: {s.output} launches -> {s.output_path}")


def _failure(exc: FeedError) -> tuple[int, str]:
    if isinstance(exc, ConfigError):
        return EXIT_CONFIG, f"config error: {exc}"
    if isinstance(exc, RateLimitedError):
        text = f"rate limited: {exc}"
        if exc.retry_after_seconds is not None:
            text += f" (retry after {exc.retry_after_seconds}s)"
        return EXIT_RATE_LIMITED, text
    if isinstance(exc, UpstreamError):
        return EXIT_UPSTREAM, f"upstream error: {exc}"
    if isinstance(exc, ValidationFailedError):
        return EXIT_VALIDATION, f"validation failed: {exc}"
    if isinstance(exc, EmptyFeedError):
        return EXIT_EMPTY, f"empty feed: {exc}"
    return EXIT_UNEXPECTED, f"error: {exc}"


def _write_output(build: bool) -> None:
    """判定を標準出力と GitHub Actions のステップ出力（``GITHUB_OUTPUT``）に書く。"""
    line = f"build={'true' if build else 'false'}"
    print(line)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _guard(args: argparse.Namespace, get: JsonGetter | None, now: datetime | None) -> int:
    """起動の間隔のガード。判定にかかわらず終了コード 0（結果は ``build=`` の出力）。"""
    now = now or datetime.now(UTC)
    interval = timedelta(seconds=args.min_interval)
    try:
        if get is None:
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                raise ConfigError("GITHUB_TOKEN is not set")
            get = github_json_getter(token)
        runs = recent_runs(
            get,
            api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            repo=args.repo,
            workflow=args.workflow,
            current_run_id=args.run_id,
            now=now,
            min_interval=interval,
        )
        decision = decide(runs, current_run_id=args.run_id, now=now, min_interval=interval)
    except Exception as exc:  # noqa: BLE001 - 判定できないときは飛ばす側に倒す（run_guard の説明）
        text = " ".join(f"{type(exc).__name__}: {exc}".split())
        print(f"::warning title=run guard failed::skipping this run: {text}", file=sys.stderr)
        _write_output(False)
        return EXIT_OK
    print(f"guard: {decision.reason}", file=sys.stderr)
    _write_output(decision.build)
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    github_get: JsonGetter | None = None,
    now: datetime | None = None,
) -> int:
    """CLI エントリ。終了コードを返す。``github_get`` と ``now`` はテスト用の差し替え。"""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv)
    if args.command == "guard":
        return _guard(args, github_get, now)
    try:
        settings = resolve_settings(endpoint=args.endpoint, repo_root=args.repo_root)
        overrides = CountryOverrides.load(settings.overrides_path)
        fetcher: Fetcher
        if args.input_dir is not None:
            fetcher = DirectoryFetcher(args.input_dir)
        else:
            fetcher = Ll2HttpFetcher(settings.base_url, settings.api_key)
        summary = run(
            fetcher,
            overrides=overrides,
            schema_path=settings.schema_path,
            out_dir=args.out,
        )
    except FeedError as exc:
        code, text = _failure(exc)
        print(" ".join(text.split()), file=sys.stderr)
        return code
    except Exception as exc:  # noqa: BLE001 - 最後の砦。想定外のエラーは終了コード 1
        logging.getLogger(__name__).exception("unexpected error")
        print(" ".join(f"unexpected error: {type(exc).__name__}: {exc}".split()), file=sys.stderr)
        return EXIT_UNEXPECTED
    _print_summary(summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

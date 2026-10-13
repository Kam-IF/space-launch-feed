"""CLI: python -m launch_feed build --out <dir> [--endpoint prod|dev] [--input-dir <dir>]."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
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
from launch_feed.settings import DirectoryFetcher, resolve_settings

EXIT_OK = 0
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
    return 1, f"error: {exc}"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI エントリ。終了コードを返す。"""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv)
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
    _print_summary(summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

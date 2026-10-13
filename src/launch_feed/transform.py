"""LL2 の launch をフィードの launch へ変換する。必須項目の欠落は除外して記録する。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from launch_feed.countries import CountryOverrides, resolve_countries

logger = logging.getLogger(__name__)

STATUS_CODES: dict[int, str] = {
    1: "go",
    2: "tbd",
    8: "tbc",
    5: "hold",
    6: "inFlight",
    3: "success",
    4: "failure",
    7: "partialFailure",
    9: "deployed",
}

NET_PRECISIONS: dict[int, str] = {
    0: "second",
    1: "minute",
    2: "hour",
    3: "morning",
    4: "afternoon",
    5: "day",
    6: "week",
    7: "month",
    **dict.fromkeys(range(8, 12), "quarter"),
    **dict.fromkeys(range(12, 14), "half"),
    14: "year",
    15: "fiscalYear",
    16: "decade",
}

_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SkippedLaunch:
    """必須項目の欠落などで除外した打ち上げ。"""

    launch_id: str | None
    reason: str


@dataclass(frozen=True)
class TransformResult:
    """変換結果。launches は入力の順序を保つ。"""

    launches: list[dict[str, object]]
    skipped: list[SkippedLaunch]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    """空でない文字列ならそのまま返す。それ以外は None。"""
    return value if isinstance(value, str) and value.strip() else None


def _int_id(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def format_utc(value: Any) -> str | None:
    """ISO 8601 文字列を UTC の ``YYYY-MM-DDTHH:MM:SSZ`` にする。

    秒未満は切り捨て。文字列でない・解釈できない・UTC オフセットを持たない値は None。
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError):  # 例: 9999-12-31T23:30:00-01:00
        return None


def _status(launch: Mapping[str, Any]) -> dict[str, object]:
    status = _mapping(launch.get("status"))
    source_id = _int_id(status.get("id"))
    code = STATUS_CODES.get(source_id, _UNKNOWN) if source_id is not None else _UNKNOWN
    return {"code": code, "sourceId": source_id, "sourceName": _text(status.get("name"))}


def _net_precision(launch: Mapping[str, Any]) -> str:
    precision_id = _int_id(_mapping(launch.get("net_precision")).get("id"))
    if precision_id is None:
        return _UNKNOWN
    return NET_PRECISIONS.get(precision_id, _UNKNOWN)


def _normalize_http_url(value: Any) -> str | None:
    """http(s) の URL を返す（スキームは小文字化、前後空白は除去）。不正なら None。"""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or any(ch.isspace() for ch in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return parts.scheme + url[len(parts.scheme) :]


def _links(value: Any) -> list[dict[str, object]]:
    """リンク配列を変換する。http(s) 以外・ホストなし・空白を含む URL は捨てる。スキームは小文字へ正規化する。priority があれば昇順。"""
    if not isinstance(value, list):
        return []
    entries = [e for e in value if isinstance(e, Mapping)]

    def priority(entry: Mapping[str, Any]) -> tuple[int, int]:
        p = _int_id(entry.get("priority"))
        return (0, p) if p is not None else (1, 0)

    if any(_int_id(e.get("priority")) is not None for e in entries):
        entries.sort(key=priority)  # 安定ソート。priority なしは末尾
    links: list[dict[str, object]] = []
    for entry in entries:
        url = _normalize_http_url(entry.get("url"))
        if url is None:
            continue
        source = entry.get("source")
        title = entry.get("title")
        links.append(
            {
                "url": url,
                "title": title if isinstance(title, str) else None,
                "source": source if isinstance(source, str) else None,
            }
        )
    return links


def _rocket_name(launch: Mapping[str, Any]) -> str | None:
    configuration = _mapping(_mapping(launch.get("rocket")).get("configuration"))
    return _text(configuration.get("full_name")) or _text(configuration.get("name"))


def _transform_one(
    launch: Mapping[str, Any], overrides: CountryOverrides
) -> dict[str, object] | str:
    """1 件を変換する。必須欠落のときは除外理由（str）を返す。"""
    launch_id = _text(launch.get("id"))
    if launch_id is None:
        return "missing or invalid 'id'"
    name = _text(launch.get("name"))
    if name is None:
        return "missing or invalid 'name'"
    net = format_utc(launch.get("net"))
    if net is None:
        return "missing or unparseable 'net'"

    mission = _mapping(launch.get("mission"))
    pad = _mapping(launch.get("pad"))
    webcast_live = launch.get("webcast_live")
    return {
        "id": launch_id,
        "name": name,
        "missionName": _text(mission.get("name")),
        "missionDescription": _text(mission.get("description")),
        "missionType": _text(mission.get("type")),
        "orbitName": _text(_mapping(mission.get("orbit")).get("name")),
        "rocketName": _rocket_name(launch),
        "providerName": _text(_mapping(launch.get("launch_service_provider")).get("name")),
        "padName": _text(pad.get("name")),
        "locationName": _text(_mapping(pad.get("location")).get("name")),
        "net": net,
        "netPrecision": _net_precision(launch),
        "windowStart": format_utc(launch.get("window_start")),
        "windowEnd": format_utc(launch.get("window_end")),
        "status": _status(launch),
        "webcastLive": webcast_live if isinstance(webcast_live, bool) else False,
        "videoUrls": _links(launch.get("vid_urls")),
        "infoUrls": _links(launch.get("info_urls")),
        "countries": resolve_countries(launch, overrides).to_feed_dict(),
        "lastUpdated": format_utc(launch.get("last_updated")),
    }


def transform_launches(
    raw: Sequence[Mapping[str, Any]], overrides: CountryOverrides
) -> TransformResult:
    """LL2 の launch 列をフィードの launch 列へ変換する。

    入力の順序を保つ（並べ替え・重複排除は呼び出し側の責務）。
    id・name・net が欠ける／解釈できない打ち上げは除外し、skipped に理由を残す。
    """
    launches: list[dict[str, object]] = []
    skipped: list[SkippedLaunch] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            skipped.append(SkippedLaunch(None, "not an object"))
            logger.warning("skipped launch at position %d: not an object", index)
            continue
        result = _transform_one(item, overrides)
        if isinstance(result, str):
            launch_id = _text(item.get("id"))
            skipped.append(SkippedLaunch(launch_id, result))
            logger.warning(
                "skipped launch id=%s position=%d: %s", launch_id, index, result
            )
        else:
            launches.append(result)
    return TransformResult(launches=launches, skipped=skipped)

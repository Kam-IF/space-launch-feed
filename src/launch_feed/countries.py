"""関係国（役割ごとの ISO 3166-1 alpha-2 国コード）の算出と補正表の読込。"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from launch_feed.errors import ConfigError

logger = logging.getLogger(__name__)

_CODE_PATTERN = re.compile(r"[A-Z]{2}")


def _normalize_code(value: Any) -> str | None:
    """国コードを大文字 2 文字に正規化する。形式外・非文字列は None。"""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if _CODE_PATTERN.fullmatch(code) else None


def _normalize_codes(values: Iterable[Any]) -> tuple[str, ...]:
    """正規化し、形式外を捨て、重複を除いてソートした tuple を返す。"""
    return tuple(sorted({c for c in map(_normalize_code, values) if c is not None}))


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_id(value: Any) -> int | None:
    """ID として使える整数（bool は除く）なら返す。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_table(name: str, raw: Any) -> dict[int, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"country_overrides: '{name}' must be an object")
    table: dict[int, tuple[str, ...]] = {}
    for key, codes in raw.items():
        if not (isinstance(key, str) and key.isascii() and key.isdecimal()):
            raise ConfigError(
                f"country_overrides: '{name}' key {key!r} is not a numeric ID"
            )
        entity_id = int(key)
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            raise ConfigError(
                f"country_overrides: '{name}.{key}' must be an array of strings"
            )
        normalized = _normalize_codes(codes)
        if any(_normalize_code(c) is None for c in codes):
            raise ConfigError(
                f"country_overrides: '{name}.{key}' has an invalid country code: {codes!r}"
            )
        table[entity_id] = normalized
    return table


@dataclass(frozen=True)
class CountryOverrides:
    """提供元の国コードを置き換える補正表（agency ID / location ID → 国コード）。"""

    agencies: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    locations: Mapping[int, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> CountryOverrides:
        """補正なしの表を返す。"""
        return cls({}, {})

    @classmethod
    def load(cls, path: Path) -> CountryOverrides:
        """JSON ファイルから読み込む。不正なら ConfigError。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read country overrides {path}: {exc}") from exc
        except ValueError as exc:
            raise ConfigError(f"country overrides {path} is not valid JSON: {exc}") from exc
        if not isinstance(data, Mapping):
            raise ConfigError("country_overrides: top level must be an object")
        for key in ("agencies", "locations"):
            if key not in data:
                raise ConfigError(f"country_overrides: missing '{key}'")
        return cls(
            agencies=_parse_table("agencies", data["agencies"]),
            locations=_parse_table("locations", data["locations"]),
        )


@dataclass(frozen=True)
class LaunchCountries:
    """打ち上げ 1 件の役割別の関係国。各 tuple は重複なしのソート済み。"""

    launch_site: tuple[str, ...] = ()
    launch_provider: tuple[str, ...] = ()
    payload_operator: tuple[str, ...] = ()

    def to_feed_dict(self) -> dict[str, list[str]]:
        """フィード出力用の dict を返す。"""
        return {
            "launchSite": list(self.launch_site),
            "launchProvider": list(self.launch_provider),
            "payloadOperator": list(self.payload_operator),
        }


def _country_codes(entity: Any) -> list[Any]:
    """agency 等の `country[].alpha_2_code` を集める。"""
    return [_as_mapping(c).get("alpha_2_code") for c in _as_list(_as_mapping(entity).get("country"))]


def _launch_site(launch: Mapping[str, Any], overrides: CountryOverrides) -> tuple[str, ...]:
    pad = _as_mapping(launch.get("pad"))
    location = _as_mapping(pad.get("location"))
    location_id = _int_id(location.get("id"))
    if location_id is not None and location_id in overrides.locations:
        return _normalize_codes(overrides.locations[location_id])
    codes = _normalize_codes([_as_mapping(location.get("country")).get("alpha_2_code")])
    if codes:
        return codes
    return _normalize_codes([_as_mapping(pad.get("country")).get("alpha_2_code")])


def _agency_codes(agency: Any, overrides: CountryOverrides) -> list[Any]:
    agency_id = _int_id(_as_mapping(agency).get("id"))
    if agency_id is not None and agency_id in overrides.agencies:
        return list(overrides.agencies[agency_id])
    return _country_codes(agency)


def resolve_countries(launch: Mapping[str, Any], overrides: CountryOverrides) -> LaunchCountries:
    """LL2 の打ち上げ（detailed）から役割別の関係国を算出する。

    欠落・型違いは例外にせず空になる。補正表の ID は提供元の値を置き換える。
    """
    provider = _agency_codes(launch.get("launch_service_provider"), overrides)
    operators: list[Any] = []
    for agency in _as_list(_as_mapping(launch.get("mission")).get("agencies")):
        operators.extend(_agency_codes(agency, overrides))
    return LaunchCountries(
        launch_site=_launch_site(launch, overrides),
        launch_provider=_normalize_codes(provider),
        payload_operator=_normalize_codes(operators),
    )

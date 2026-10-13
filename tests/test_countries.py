"""countries.py のテスト。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from launch_feed.countries import CountryOverrides, LaunchCountries, resolve_countries
from launch_feed.errors import ConfigError

Loader = Callable[[str], Any]


@pytest.fixture
def cases(load_fixture: Loader) -> dict[str, dict[str, Any]]:
    return {r["_case"]: r for r in load_fixture("ll2_edge_cases.json")["results"]}


def _ov(**kw: dict[int, tuple[str, ...]]) -> CountryOverrides:
    return CountryOverrides(agencies=kw.get("agencies", {}), locations=kw.get("locations", {}))


def test_h3_tanegashima(cases: dict[str, Any]) -> None:
    c = resolve_countries(cases["h3_tanegashima"], CountryOverrides.empty())
    assert c.launch_site == ("JP",)
    assert c.launch_provider == ("JP",)
    assert c.payload_operator == ()


def test_japan_payload_us_pad(cases: dict[str, Any]) -> None:
    c = resolve_countries(cases["japan_payload_us_pad"], CountryOverrides.empty())
    assert c.launch_site == ("US",)
    assert c.launch_provider == ("US",)
    assert "JP" in c.payload_operator


def test_overlap_deduplicated_sorted(cases: dict[str, Any]) -> None:
    c = resolve_countries(cases["mission_agencies_overlap"], CountryOverrides.empty())
    assert c.payload_operator == ("JP", "US")


def test_lsp_country_empty_and_override_fills(cases: dict[str, Any]) -> None:
    launch = cases["lsp_country_empty"]
    assert resolve_countries(launch, CountryOverrides.empty()).launch_provider == ()
    c = resolve_countries(launch, _ov(agencies={555: ("JP",)}))
    assert c.launch_provider == ("JP",)


def test_override_replaces_existing(cases: dict[str, Any]) -> None:
    launch = json.loads(json.dumps(cases["mission_agencies_overlap"]))
    launch["mission"]["agencies"].append(
        {"id": 27, "country": [{"alpha_2_code": "DE"}, {"alpha_2_code": "IT"}]}
    )
    assert resolve_countries(launch, CountryOverrides.empty()).payload_operator == ("DE", "IT", "JP", "US")
    c = resolve_countries(launch, _ov(agencies={27: ("FR",)}))
    assert c.payload_operator == ("FR", "JP", "US")


def test_location_override_replaces_site(cases: dict[str, Any]) -> None:
    c = resolve_countries(cases["japan_payload_us_pad"], _ov(locations={11: ("jp",)}))
    assert c.launch_site == ("JP",)


def test_pad_country_fallback() -> None:
    launch = {"pad": {"country": {"alpha_2_code": "in"}, "location": {"id": 1}}}
    assert resolve_countries(launch, CountryOverrides.empty()).launch_site == ("IN",)


def test_invalid_codes(cases: dict[str, Any]) -> None:
    c = resolve_countries(cases["invalid_country_codes"], CountryOverrides.empty())
    assert c.launch_provider == ("JP",)
    assert c.payload_operator == ()
    assert c.launch_site == ("FR",)  # location の DEU は捨て、pad.country の fr を採る


@pytest.mark.parametrize(
    "launch",
    [{}, {"pad": None, "launch_service_provider": None, "mission": None},
     {"pad": "x", "launch_service_provider": [], "mission": {"agencies": "x"}},
     {"mission": {"agencies": [None, 3, {"country": None}, {"country": [None, 5, {"alpha_2_code": 7}]}]}},
     {"launch_service_provider": {"id": "abc", "country": {"alpha_2_code": "US"}}}],
)
def test_never_raises_on_bad_shapes(launch: dict[str, Any]) -> None:
    c = resolve_countries(launch, CountryOverrides.empty())
    assert c == LaunchCountries((), (), ())
    assert c.to_feed_dict() == {"launchSite": [], "launchProvider": [], "payloadOperator": []}


def test_to_feed_dict(cases: dict[str, Any]) -> None:
    d = resolve_countries(cases["japan_payload_us_pad"], CountryOverrides.empty()).to_feed_dict()
    assert d == {"launchSite": ["US"], "launchProvider": ["US"], "payloadOperator": ["JP"]}


# --- CountryOverrides ---------------------------------------------------

def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "o.json"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_ok(tmp_path: Path) -> None:
    o = CountryOverrides.load(_write(tmp_path, '{"agencies": {"1091": ["jp", "JP"]}, "locations": {"166": ["JP"]}}'))
    assert o.agencies == {1091: ("JP",)}
    assert o.locations == {166: ("JP",)}


def test_load_repo_config() -> None:
    path = Path(__file__).parent.parent / "config" / "country_overrides.json"
    assert CountryOverrides.load(path) == CountryOverrides.empty()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"agencies": {}}',
        '{"agencies": [], "locations": {}}',
        '{"agencies": {"abc": ["JP"]}, "locations": {}}',
        '{"agencies": {" 12 ": ["JP"]}, "locations": {}}',
        '{"agencies": {"-1": ["JP"]}, "locations": {}}',
        '{"agencies": {"+1": ["JP"]}, "locations": {}}',
        '{"agencies": {"1_0": ["JP"]}, "locations": {}}',
        '{"agencies": {}, "locations": {"\\u0661": ["JP"]}}',
        '{"agencies": {"": ["JP"]}, "locations": {}}',
        '{"agencies": {"1": "JP"}, "locations": {}}',
        '{"agencies": {"1": [1]}, "locations": {}}',
        '{"agencies": {}, "locations": {"1": ["USA"]}}',
        '{"agencies": {}, "locations": {"1": ["1A"]}}',
    ],
)
def test_load_errors(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        CountryOverrides.load(_write(tmp_path, text))


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        CountryOverrides.load(tmp_path / "nope.json")


# --- 実データ -----------------------------------------------------------

def test_real_data(load_fixture: Loader) -> None:
    launches = load_fixture("run/upcoming_p1.json")["results"] + load_fixture("run/upcoming_p2.json")["results"]
    assert launches
    empty_all = 0
    for launch in launches:
        c = resolve_countries(launch, CountryOverrides.empty())
        for codes in (c.launch_site, c.launch_provider, c.payload_operator):
            assert codes == tuple(sorted(set(codes)))
            assert all(re.fullmatch(r"[A-Z]{2}", x) for x in codes)
        if not (c.launch_site or c.launch_provider or c.payload_operator):
            empty_all += 1
    print(f"all-empty: {empty_all}/{len(launches)}")

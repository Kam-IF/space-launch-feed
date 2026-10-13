"""transform_launches のテスト（要件 2.1, 2.2, 2.4–2.7）。"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from launch_feed.countries import CountryOverrides
from launch_feed.transform import format_utc, transform_launches
from launch_feed.validate import validate_feed

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "launches.v1.schema.json"

LAUNCH_KEYS = [
    "id", "name", "missionName", "missionDescription", "missionType", "orbitName",
    "rocketName", "providerName", "padName", "locationName", "net", "netPrecision",
    "windowStart", "windowEnd", "status", "webcastLive", "videoUrls", "infoUrls",
    "countries", "lastUpdated",
]  # fmt: skip

EMPTY = CountryOverrides.empty()
Loader = Callable[[str], Any]


def edge_cases(load_fixture: Loader) -> dict[str, dict[str, Any]]:
    return {c["_case"]: c for c in load_fixture("ll2_edge_cases.json")["results"]}


def one(raw: dict[str, Any]) -> dict[str, Any]:
    result = transform_launches([raw], EMPTY)
    assert result.skipped == []
    return result.launches[0]


@pytest.fixture
def base(load_fixture: Loader) -> dict[str, Any]:
    return copy.deepcopy(edge_cases(load_fixture)["precision_month"])


def test_output_keys_match_table_in_order(load_fixture: Loader) -> None:
    result = transform_launches(load_fixture("ll2_edge_cases.json")["results"], EMPTY)
    assert result.launches
    for launch in result.launches:
        assert list(launch) == LAUNCH_KEYS
        assert "_case" not in launch
        assert set(launch["status"]) == {"code", "sourceId", "sourceName"}  # type: ignore[call-overload]


@pytest.mark.parametrize(
    ("case", "fragment"),
    [
        ("missing_id", "id"),
        ("missing_name", "name"),
        ("missing_net", "net"),
        ("net_unparseable", "net"),
    ],
)
def test_required_missing_is_skipped(load_fixture: Loader, case: str, fragment: str) -> None:
    raw = edge_cases(load_fixture)[case]
    result = transform_launches([raw], EMPTY)
    assert result.launches == []
    assert len(result.skipped) == 1
    assert fragment in result.skipped[0].reason
    assert result.skipped[0].launch_id == raw.get("id")


def test_skipped_does_not_stop_others_and_order_is_kept(load_fixture: Loader) -> None:
    cases = edge_cases(load_fixture)
    raw = [cases["precision_year"], cases["missing_id"], cases["precision_month"]]
    result = transform_launches(raw, EMPTY)
    assert [x["id"] for x in result.launches] == [raw[0]["id"], raw[2]["id"]]
    assert [s.launch_id for s in result.skipped] == [None]


def test_non_object_entry_is_skipped() -> None:
    result = transform_launches(["x"], EMPTY)  # type: ignore[list-item]
    assert result.launches == [] and result.skipped[0].launch_id is None


def test_optional_missing_becomes_null(load_fixture: Loader) -> None:
    cases = edge_cases(load_fixture)
    mission_null = one(cases["mission_null"])
    for key in ("missionName", "missionDescription", "missionType", "orbitName"):
        assert mission_null[key] is None
    assert one(cases["rocket_without_configuration"])["rocketName"] is None
    assert one(cases["window_start_numeric"])["windowStart"] is None
    assert one(cases["vid_urls_null"])["videoUrls"] == []
    assert one(cases["status_missing"])["status"] == {
        "code": "unknown", "sourceId": None, "sourceName": None,
    }  # fmt: skip


def test_empty_and_wrong_type_strings_become_null(base: dict[str, Any]) -> None:
    base["mission"]["name"] = ""
    base["mission"]["type"] = 5
    base["pad"]["name"] = None
    out = one(base)
    assert out["missionName"] is None
    assert out["missionType"] is None
    assert out["padName"] is None


def test_rocket_name_falls_back_to_name(base: dict[str, Any]) -> None:
    base["rocket"]["configuration"]["full_name"] = ""
    assert one(base)["rocketName"] == "Falcon 9"
    base["rocket"]["configuration"]["full_name"] = "Full"
    assert one(base)["rocketName"] == "Full"


@pytest.mark.parametrize(
    ("status_id", "code"),
    [(1, "go"), (2, "tbd"), (8, "tbc"), (5, "hold"), (6, "inFlight"), (3, "success"),
     (4, "failure"), (7, "partialFailure"), (9, "deployed"), (99, "unknown")],
)  # fmt: skip
def test_status_code_mapping(base: dict[str, Any], status_id: int, code: str) -> None:
    base["status"] = {"id": status_id, "name": "N"}
    assert one(base)["status"] == {"code": code, "sourceId": status_id, "sourceName": "N"}


def test_status_bool_id_is_not_accepted(base: dict[str, Any]) -> None:
    base["status"] = {"id": True, "name": "N"}
    assert one(base)["status"] == {"code": "unknown", "sourceId": None, "sourceName": "N"}


@pytest.mark.parametrize(
    ("precision_id", "value"),
    [(0, "second"), (1, "minute"), (2, "hour"), (3, "morning"), (4, "afternoon"),
     (5, "day"), (6, "week"), (7, "month"), (8, "quarter"), (9, "quarter"),
     (10, "quarter"), (11, "quarter"), (12, "half"), (13, "half"), (14, "year"),
     (15, "fiscalYear"), (16, "decade"), (17, "unknown"), (-1, "unknown"), (42, "unknown")],
)  # fmt: skip
def test_net_precision_mapping(base: dict[str, Any], precision_id: int, value: str) -> None:
    base["net_precision"] = {"id": precision_id}
    assert one(base)["netPrecision"] == value


def test_net_precision_missing_or_invalid_is_unknown(load_fixture: Loader) -> None:
    cases = edge_cases(load_fixture)
    assert one(cases["net_precision_missing"])["netPrecision"] == "unknown"
    assert one(cases["net_precision_id_unknown"])["netPrecision"] == "unknown"
    assert one(cases["precision_month"])["netPrecision"] == "month"
    assert one(cases["precision_quarter_q4"])["netPrecision"] == "quarter"
    assert one(cases["precision_year"])["netPrecision"] == "year"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-01T12:00:00Z", "2026-11-01T12:00:00Z"),
        ("2026-11-01T12:00:00+00:00", "2026-11-01T12:00:00Z"),
        ("2026-11-01T21:30:00+09:00", "2026-11-01T12:30:00Z"),
        ("2026-11-01T00:30:00+09:00", "2026-10-31T15:30:00Z"),
        ("2026-11-01T12:00:00.987654Z", "2026-11-01T12:00:00Z"),
        ("2026-11-01T12:00:00", None),
        ("garbage", None),
        ("", None),
        (12345, None),
        (None, None),
    ],
)
def test_format_utc(raw: Any, expected: str | None) -> None:
    assert format_utc(raw) == expected


def test_datetime_fields_are_normalized(base: dict[str, Any]) -> None:
    base["net"] = "2026-11-01T21:00:00.5+09:00"
    base["window_start"] = "2026-11-01T21:00:00+09:00"
    base["last_updated"] = "bad"
    out = one(base)
    assert out["net"] == "2026-11-01T12:00:00Z"
    assert out["windowStart"] == "2026-11-01T12:00:00Z"
    assert out["lastUpdated"] is None


def test_webcast_live_only_bool(base: dict[str, Any]) -> None:
    assert one(base)["webcastLive"] is False
    base["webcast_live"] = True
    assert one(base)["webcastLive"] is True
    base["webcast_live"] = "true"
    assert one(base)["webcastLive"] is False


def test_links_filter_schemes(load_fixture: Loader) -> None:
    out = one(edge_cases(load_fixture)["links_mixed_schemes"])
    for key, path in (("videoUrls", "v"), ("infoUrls", "i")):
        assert out[key] == [
            {"url": f"https://example.com/{path}", "title": "https", "source": "example.com"},
            {"url": f"http://example.com/{path}", "title": "http", "source": "example.com"},
        ]


def test_links_case_insensitive_scheme_and_field_types(base: dict[str, Any]) -> None:
    base["vid_urls"] = [
        {"url": "HTTPS://Example.com/A", "title": 3},
        {"url": None},
        "junk",
        {"url": "//example.com"},
    ]
    assert one(base)["videoUrls"] == [
        {"url": "https://Example.com/A", "title": None, "source": None}
    ]
    base["vid_urls"] = {"url": "https://x"}
    assert one(base)["videoUrls"] == []


def test_links_sorted_by_priority_stably(base: dict[str, Any]) -> None:
    base["info_urls"] = [
        {"url": "https://a", "priority": 10},
        {"url": "https://b", "priority": 1},
        {"url": "https://c", "priority": 10},
        {"url": "https://d"},
    ]
    assert [x["url"] for x in one(base)["infoUrls"]] == [
        "https://b", "https://a", "https://c", "https://d",
    ]  # fmt: skip
    base["info_urls"] = [{"url": "https://z"}, {"url": "https://y"}]
    assert [x["url"] for x in one(base)["infoUrls"]] == ["https://z", "https://y"]


def test_countries_use_resolver(load_fixture: Loader) -> None:
    out = one(edge_cases(load_fixture)["h3_tanegashima"])
    assert set(out["countries"]) == {"launchSite", "launchProvider", "payloadOperator"}  # type: ignore[call-overload]
    assert out["countries"]["launchProvider"] == ["JP"]  # type: ignore[call-overload]


def test_real_data_transforms_and_validates(load_fixture: Loader) -> None:
    raw = load_fixture("run/upcoming_p1.json")["results"] + load_fixture("run/upcoming_p2.json")["results"]
    assert raw
    result = transform_launches(raw, EMPTY)
    assert result.skipped == []
    assert len(result.launches) == len(raw)
    assert all(list(x) == LAUNCH_KEYS for x in result.launches)
    feed = {
        "schemaVersion": 1,
        "generatedAt": "2026-10-13T00:00:00Z",
        "source": {"name": "The Space Devs – Launch Library 2", "url": "https://thespacedevs.com/llapi"},
        "launches": result.launches,
    }
    validate_feed(feed, SCHEMA_PATH)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://example.com/a", "https://example.com/a"),
        ("Http://x", "http://x"),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("HtTpS://Example.com/Path?Q=A", "https://Example.com/Path?Q=A"),
        ("   ", None),
        ("https://", None),
        ("HTTP://", None),
        ("https:// example.com", None),
        ("https://exa mple.com", None),
        ("ftp://example.com", None),
    ],
)
def test_links_scheme_is_lowercased_and_invalid_dropped(
    base: dict[str, Any], raw: str, expected: str | None
) -> None:
    base["info_urls"] = [{"url": raw}]
    urls = [x["url"] for x in one(base)["infoUrls"]]
    assert urls == ([] if expected is None else [expected])


def test_normalized_links_pass_schema(base: dict[str, Any]) -> None:
    base["info_urls"] = [{"url": "HTTPS://example.com/a"}]
    assert one(base)["infoUrls"][0]["url"].startswith("https://")


@pytest.mark.parametrize("raw", ["9999-12-31T23:30:00-01:00", "0001-01-01T00:00:00+09:00"])
def test_format_utc_overflow_is_none(raw: str) -> None:
    assert format_utc(raw) is None


def test_overflowing_datetimes_net_excluded_optional_null(base: dict[str, Any]) -> None:
    base["window_start"] = "9999-12-31T23:30:00-01:00"
    assert one(base)["windowStart"] is None
    base["net"] = "9999-12-31T23:30:00-01:00"
    result = transform_launches([base], CountryOverrides.empty())
    assert result.launches == []
    assert len(result.skipped) == 1

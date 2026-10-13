import copy
from pathlib import Path

import pytest

from launch_feed.errors import EmptyFeedError, ValidationFailedError
from launch_feed.validate import validate_feed

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "launches.v1.schema.json"


def make_launch() -> dict:
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "name": "H3 | Example",
        "missionName": "Example",
        "missionDescription": None,
        "missionType": None,
        "orbitName": None,
        "rocketName": "H3",
        "providerName": None,
        "padName": None,
        "locationName": None,
        "net": "2026-02-01T03:00:00Z",
        "netPrecision": "minute",
        "windowStart": None,
        "windowEnd": "2026-02-01T04:00:00Z",
        "status": {"code": "go", "sourceId": 1, "sourceName": "Go for Launch"},
        "webcastLive": False,
        "videoUrls": [],
        "infoUrls": [],
        "countries": {"launchSite": ["JP"], "launchProvider": ["JP"], "payloadOperator": []},
        "lastUpdated": None,
    }


def make_feed(n: int = 1) -> dict:
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-01-01T00:00:00Z",
        "source": {"name": "The Space Devs", "url": "https://thespacedevs.com/llapi"},
        "launches": [copy.deepcopy(make_launch()) for _ in range(n)],
    }


def test_valid_feed_passes() -> None:
    validate_feed(make_feed(), SCHEMA_PATH)


def test_extra_key_reports_path() -> None:
    feed = make_feed(2)
    feed["launches"][1]["extra"] = 1
    with pytest.raises(ValidationFailedError) as exc:
        validate_feed(feed, SCHEMA_PATH)
    assert "/launches/1" in str(exc.value)
    assert "1 error" in str(exc.value)


def test_wrong_type_reports_path() -> None:
    feed = make_feed()
    feed["launches"][0]["status"]["code"] = "bogus"
    with pytest.raises(ValidationFailedError, match="/launches/0/status/code"):
        validate_feed(feed, SCHEMA_PATH)
    feed = make_feed()
    feed["launches"][0]["webcastLive"] = "false"
    with pytest.raises(ValidationFailedError, match="/launches/0/webcastLive"):
        validate_feed(feed, SCHEMA_PATH)


def test_missing_schema_version() -> None:
    feed = make_feed()
    del feed["schemaVersion"]
    with pytest.raises(ValidationFailedError, match="schemaVersion"):
        validate_feed(feed, SCHEMA_PATH)


def test_empty_launches_is_empty_feed_error() -> None:
    feed = make_feed()
    feed["launches"] = []
    with pytest.raises(EmptyFeedError) as exc:
        validate_feed(feed, SCHEMA_PATH)
    assert not isinstance(exc.value, ValidationFailedError)


def test_launches_not_array_is_validation_error() -> None:
    feed = make_feed()
    feed["launches"] = "x"
    with pytest.raises(ValidationFailedError, match="/launches"):
        validate_feed(feed, SCHEMA_PATH)


def test_at_most_five_errors_with_total() -> None:
    feed = make_feed(7)
    for launch in feed["launches"]:
        launch["webcastLive"] = "no"
    with pytest.raises(ValidationFailedError) as exc:
        validate_feed(feed, SCHEMA_PATH)
    msg = str(exc.value)
    assert "7 error" in msg
    assert msg.count("/webcastLive") == 5
    assert "/launches/0/webcastLive" in msg
    assert "/launches/6/webcastLive" not in msg

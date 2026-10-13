import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "launches.v1.schema.json"


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator(schema):
    return Draft202012Validator(schema)


def make_feed():
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-01-01T00:00:00Z",
        "source": {
            "name": "The Space Devs – Launch Library 2",
            "url": "https://thespacedevs.com/llapi",
        },
        "launches": [
            {
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
                "videoUrls": [{"url": "https://example.com/v", "title": None, "source": None}],
                "infoUrls": [],
                "countries": {
                    "launchSite": ["JP"],
                    "launchProvider": ["JP"],
                    "payloadOperator": [],
                },
                "lastUpdated": None,
            }
        ],
    }


def errors(validator, feed):
    return list(validator.iter_errors(feed))


def test_valid_minimal_feed_passes(validator):
    assert errors(validator, make_feed()) == []


def test_extra_launch_key_fails(validator):
    feed = make_feed()
    feed["launches"][0]["extra"] = 1
    assert errors(validator, feed)


def test_wrong_type_fails(validator):
    feed = make_feed()
    feed["launches"][0]["webcastLive"] = "false"
    assert errors(validator, feed)


def test_missing_schema_version_fails(validator):
    feed = make_feed()
    del feed["schemaVersion"]
    assert errors(validator, feed)


def test_empty_launches_fails(validator):
    feed = make_feed()
    feed["launches"] = []
    assert errors(validator, feed)


def test_lowercase_country_code_fails(validator):
    feed = make_feed()
    feed["launches"][0]["countries"]["launchSite"] = ["jp"]
    assert errors(validator, feed)


def test_net_with_offset_fails(validator):
    feed = make_feed()
    feed["launches"][0]["net"] = "2026-02-01T12:00:00+09:00"
    assert errors(validator, feed)


def test_schema_is_valid_draft_2020_12(schema):
    Draft202012Validator.check_schema(schema)

"""The import form's reference document must match what the API accepts."""

import pytest

from itp.garments import (
    BODY_PROFILE_FIELDS,
    CATEGORIES,
    FIT_RANGE_BOUNDS,
    LENGTH_TYPES,
    MEASUREMENT_KEYS,
    SEASONS,
    SILHOUETTES,
    STATUSES,
    STRETCHES,
    STYLES,
    normalize_metrics,
    options_document,
)


def options():
    return options_document()


def test_options_publish_the_same_vocabulary_the_validator_enforces():
    document = options()

    assert document["categories"] == list(CATEGORIES)
    assert document["styles"] == list(STYLES)
    assert document["seasons"] == list(SEASONS)
    assert document["silhouettes"] == list(SILHOUETTES)
    assert document["stretches"] == list(STRETCHES)
    assert document["length_types"] == list(LENGTH_TYPES)
    assert document["statuses"] == list(STATUSES)
    assert [item["key"] for item in document["measurements"]] == list(MEASUREMENT_KEYS)


def test_options_fit_ranges_carry_the_enforced_bounds():
    ranges = {item["key"]: item for item in options()["fit_ranges"]}

    assert set(ranges) == set(FIT_RANGE_BOUNDS)
    for key, (low, high) in FIT_RANGE_BOUNDS.items():
        assert ranges[key]["min"] == low
        assert ranges[key]["max"] == high
        assert ranges[key]["label"]


def test_options_body_profile_matches_the_stored_fields():
    entries = options()["body_profile"]

    assert [item["key"] for item in entries] == list(BODY_PROFILE_FIELDS)
    for item in entries:
        assert item["label"] and item["min"] < item["max"]


def test_a_payload_built_from_the_options_is_accepted():
    """The form is generated from this document, so it must validate as-is."""
    document = options()
    payload = {
        "category": document["categories"][0],
        "name": "参考数据构造的商品",
        "status": "published",
        "style": document["styles"][0],
        "season": document["seasons"][0],
        "occasion": "通勤办公",
        "price_cents": document["limits"]["price_max_cents"] // 1000,
        "measurements": {
            item["key"]: item["max"] / 2 for item in document["measurements"]
        },
        "fit_ranges": {
            item["key"]: [item["min"], item["max"]] for item in document["fit_ranges"]
        },
        "attributes": {
            "silhouette": document["silhouettes"][0],
            "stretch": document["stretches"][0],
            "length_type": document["length_types"][0],
            "weight_gsm": document["limits"]["weight_gsm_min"],
            "color": "#123abc",
        },
        "tips": ["从参考数据生成"],
    }

    metrics = normalize_metrics(payload)

    assert metrics["category"] == payload["category"]
    assert metrics["attributes"]["weight_gsm"] == document["limits"]["weight_gsm_min"]


def test_the_weight_message_stays_in_chinese_with_its_bounds():
    limits = options()["limits"]
    with pytest.raises(ValueError) as error:
        normalize_metrics({
            "category": "上装", "name": "越界克重", "status": "draft",
            "attributes": {"weight_gsm": limits["weight_gsm_max"] + 1},
        })

    message = str(error.value)
    assert str(limits["weight_gsm_min"]) in message
    assert str(limits["weight_gsm_max"]) in message
    assert "整数" in message


def test_limited_fields_report_their_ceiling():
    limits = options()["limits"]

    assert limits["images_max"] >= 1
    assert limits["image_max_mb"] >= 1
    assert limits["tips_max"] >= 1
    assert limits["description_max"] >= limits["tip_max"]
    assert limits["look_items_max"] >= 2

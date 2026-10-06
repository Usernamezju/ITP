"""The recommendation service: merchant items first, catalogue as the fallback."""

import pytest

from itp.garments import MerchantStore, normalize_look, normalize_metrics
from itp.outfit_service import body_inputs, recommend
from itp.size_match import score_garment
from itp import wardrobe

MEASUREMENTS = {
    "height_cm": 170.0, "bust_cm": 88.0, "waist_cm": 70.0, "weight_kg": 58.0,
}


@pytest.fixture
def merchants(settings):
    return MerchantStore(settings.data_dir)


@pytest.fixture
def merchant(merchants):
    return merchants.create_merchant(
        name="demo-shop", display_name="示例商家", contact="demo@example.com",
        password_hash="scrypt$16384$8$1$aa$bb", quota=50,
    )


def make_garment(merchants, merchant_id, **overrides):
    payload = {
        "category": "上装", "name": "细罗纹半高领针织", "status": "published",
        "measurements": {"shoulder_cm": 39, "bust_cm": 100},
        "fit_ranges": {"height_cm": [158, 176], "bust_cm": [86, 96], "waist_cm": [64, 78]},
        "attributes": {"silhouette": "标准", "stretch": "微弹", "weight_gsm": 240,
                       "color": "#c9d6bd", "length_type": "常规"},
        "style": "通勤", "season": "四季", "occasion": "通勤办公",
    }
    payload.update(overrides)
    return merchants.create_garment(merchant_id, normalize_metrics(payload))


def trousers(merchants, merchant_id, **overrides):
    payload = {
        "category": "下装", "name": "高腰直筒西裤", "status": "published",
        "measurements": {"waist_cm": 72, "hip_cm": 104},
        "fit_ranges": {"height_cm": [162, 176], "waist_cm": [66, 76], "hip_cm": [90, 100]},
        "attributes": {"silhouette": "标准", "stretch": "无弹", "weight_gsm": 240,
                       "color": "#4a5b52", "length_type": "常规"},
        "style": "通勤", "season": "四季", "occasion": "通勤办公",
    }
    payload.update(overrides)
    return merchants.create_garment(merchant_id, normalize_metrics(payload))


def score(store, merchants, **overrides):
    """Score the numbers the browser sent; the server keeps no profile of its own."""
    return recommend(store, merchants, body_profile=dict(MEASUREMENTS), **overrides)


def make_look(merchants, merchant_id, item_ids, **overrides):
    payload = {
        "name": "柔雾通勤", "story": "上装塞进高腰裤，比例更长。", "status": "published",
        "style": "通勤", "season": "四季", "occasion": "通勤办公",
        "palette": ["#c9d6bd", "#4a5b52"], "items": list(item_ids),
    }
    payload.update(overrides)
    return merchants.create_look(merchant_id, normalize_look(payload))


def test_published_looks_are_scored_with_measurements(store, merchants, merchant):
    top = make_garment(merchants, merchant["id"])
    bottom = trousers(merchants, merchant["id"])
    make_look(merchants, merchant["id"], [top["id"], bottom["id"]])

    report = score(store, merchants, limit=3)

    assert len(report["recommendations"]) == 1
    outfit = report["recommendations"][0]
    assert outfit["name"] == "柔雾通勤"
    assert outfit["origin"] == "database"
    assert outfit["items"][0]["name"] == "细罗纹半高领针织"
    assert 0.5 < outfit["score"] <= 1.0
    # Measured values are used directly and labelled as such.
    assert report["analysis"]["body"]["height_cm"] == {
        "value": 170.0, "source": "input", "confidence": 1.0,
    }
    dimensions = {dimension["key"]: dimension for dimension in outfit["fit"]["dimensions"]}
    assert dimensions["bust_cm"]["state"] == "fit"
    assert dimensions["bust_cm"]["body_source"] == "input"
    assert dimensions["bust_cm"]["range"] == [86.0, 96.0]
    assert outfit["fit"]["reasons"]
    assert outfit["reason"] == outfit["fit"]["reasons"][0]


def test_garments_answer_when_no_look_is_published(store, merchants, merchant):
    make_garment(merchants, merchant["id"], name="单件针织")
    make_garment(merchants, merchant["id"], category="外套", name="落肩薄外套")

    report = score(store, merchants, limit=5)

    assert len(report["recommendations"]) == 2
    for outfit in report["recommendations"]:
        assert outfit["origin"] == "database"
        assert len(outfit["items"]) == 1
        assert outfit["fit"]["dimensions"]
    # Best match first.
    scores = [outfit["score"] for outfit in report["recommendations"]]
    assert scores == sorted(scores, reverse=True)


def test_draft_items_stay_hidden_and_the_catalogue_answers(store, merchants, merchant):
    make_garment(merchants, merchant["id"], status="draft")

    report = score(store, merchants, limit=3)

    assert report["recommendations"]
    assert all(outfit["origin"] == "catalogue" for outfit in report["recommendations"])
    assert not any("fit" in outfit for outfit in report["recommendations"])


def test_a_look_whose_members_are_draft_is_skipped(store, merchants, merchant):
    hidden = make_garment(merchants, merchant["id"], status="draft", name="未发布上装")
    make_look(merchants, merchant["id"], [hidden["id"]], name="半成品套装")
    trousers(merchants, merchant["id"], name="已发布西裤")

    report = score(store, merchants, limit=5)

    names = [outfit["name"] for outfit in report["recommendations"]]
    assert "半成品套装" not in names
    assert names[0] == "已发布西裤"
    assert all(outfit["origin"] == "database" for outfit in report["recommendations"])


def test_member_images_become_the_product_gallery(store, merchants, merchant):
    top = make_garment(merchants, merchant["id"])
    bottom = trousers(merchants, merchant["id"])
    image = merchants.add_image(top["id"], "a" * 32)
    make_look(merchants, merchant["id"], [top["id"], bottom["id"]])

    report = score(store, merchants, limit=1)

    outfit = report["recommendations"][0]
    assert outfit["image_urls"] == [f"/api/garment-images/{image['id']}"]
    assert outfit["image_url"] == f"/api/garment-images/{image['id']}"


def test_filters_follow_the_published_database(store, merchants, merchant):
    make_garment(merchants, merchant["id"], style="街头", season="秋", occasion="日常出行")
    make_garment(merchants, merchant["id"], category="外套", style="街头", season="秋",
                 occasion="日常出行", name="街头外套")

    report = recommend(store, merchants, limit=5)

    assert report["filters"]["styles"] == [{"id": "街头", "count": 2}]
    assert report["filters"]["seasons"] == [{"id": "秋", "count": 2}]
    assert report["filters"]["occasions"] == [{"id": "日常出行", "count": 2}]


def test_recommendations_are_limited_and_deterministic(store, merchants, merchant):
    for index in range(5):
        make_garment(merchants, merchant["id"], name=f"单品{index}", style="通勤")

    first = score(store, merchants, limit=2)
    second = score(store, merchants, limit=2)

    assert len(first["recommendations"]) == 2
    assert first == second


def test_styles_filter_reaches_the_database_query(store, merchants, merchant):
    make_garment(merchants, merchant["id"], style="通勤", name="通勤针织")
    make_garment(merchants, merchant["id"], style="街头", name="街头卫衣")

    report = recommend(store, merchants, style="街头", limit=5)

    assert [outfit["name"] for outfit in report["recommendations"]] == ["街头卫衣"]


def test_body_inputs_merges_measurements_and_model_ratios():
    analysis = {
        "available": True,
        "labels": {"build": "修长"},
        "ratios": {"shoulder_ratio": 0.223, "waist_ratio": 0.74, "hip_ratio": 0.92,
                   "leg_ratio": 0.47, "thickness_ratio": 0.19},
        "tag_families": {"build": "slim", "volume": "light", "legs": "long-leg"},
    }
    body = body_inputs({"height_cm": 170.0, "bust_cm": 88.0}, analysis)

    assert body["height_cm"] == 170.0
    assert body["waist_cm"] is None
    assert body["model"]["available"] is True
    assert body["model"]["shoulder_ratio"] == 0.223
    assert body["model"]["tags"]["build"] == "slim"
    assert body["model"]["labels"] == {"build": "修长"}


def test_body_inputs_without_a_model_marks_it_unavailable():
    body = body_inputs(None, {"available": False, "ratios": None, "tag_families": {}})

    assert body["model"] == {"available": False}
    assert all(body[field] is None for field in
               ("height_cm", "weight_kg", "shoulder_cm", "bust_cm", "waist_cm", "hip_cm"))


def test_an_empty_database_notes_that_the_catalogue_is_used(store, merchants):
    # A customer who typed nothing and uploaded no model still gets advice.
    report = recommend(store, merchants, limit=2)

    assert report["recommendations"]
    assert all(outfit["origin"] == "catalogue" for outfit in report["recommendations"])
    assert any("商家尚未发布商品" in note for note in report["analysis"]["notes"])


def test_history_changes_garment_order_without_mutating_original_fit(store, merchants, merchant):
    work = make_garment(merchants, merchant["id"], style="通勤", name="通勤款")
    make_garment(merchants, merchant["id"], style="运动", name="运动款")
    report = score(store, merchants, history_preferences={"styles": {"通勤": 20}})
    preferred = report["recommendations"][0]
    assert preferred["name"] == "通勤款"
    expected_fit = score_garment({**MEASUREMENTS, "model": {"available": False}}, work["metrics"])
    assert preferred["fit"] == expected_fit
    assert "近期更常查看通勤风" in preferred["reason"]
    assert preferred["ranking"]["base_score"] == expected_fit["score"]


def test_historical_style_never_bypasses_explicit_merchant_filters(store, merchants, merchant):
    make_garment(merchants, merchant["id"], style="通勤", name="通勤款")
    make_garment(merchants, merchant["id"], style="运动", name="运动款")
    report = score(store, merchants, style="运动", history_preferences={"styles": {"通勤": 20}})
    assert [item["name"] for item in report["recommendations"]] == ["运动款"]


def test_incomplete_body_and_no_history_preserve_neutral_fit(store, merchants, merchant):
    make_garment(merchants, merchant["id"])
    report = recommend(store, merchants)
    item = report["recommendations"][0]
    assert item["score"] == item["fit"]["score"] == 0.5
    assert item["fit"]["confidence"] == 0.0
    assert all(dimension["state"] == "unknown" for dimension in item["fit"]["dimensions"])


def test_no_merchant_goods_rank_entire_fallback_catalogue_using_history(store, merchants):
    cold = recommend(store, merchants, limit=2)
    expected = wardrobe.outfit_report(store, limit=2)["recommendations"]
    assert [item["id"] for item in cold["recommendations"]] == [item["id"] for item in expected]
    assert [item["score"] for item in cold["recommendations"]] == [item["score"] for item in expected]
    personalized = recommend(store, merchants, limit=2, history_preferences={"styles": {"学院": 20}})
    assert personalized["recommendations"][0]["style"] == "学院"
    assert all(item["origin"] == "catalogue" for item in personalized["recommendations"])


def test_published_look_priority_survives_history_of_other_single_items(store, merchants, merchant):
    top = make_garment(merchants, merchant["id"], style="通勤")
    make_look(merchants, merchant["id"], [top["id"]], name="已发布通勤套装")
    make_garment(merchants, merchant["id"], style="运动", name="偏好运动单品")
    report = score(store, merchants, history_preferences={"styles": {"运动": 20}})
    assert [item["name"] for item in report["recommendations"]] == ["已发布通勤套装"]

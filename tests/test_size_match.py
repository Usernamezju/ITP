"""Tests for the explainable size-matching library.

Every rule that ``size_match`` documents is asserted here: the source of each
body dimension, the estimate formulas and their girth conversion, the fit
curve, weight renormalisation, the confidence contraction, the preference and
body-tag signals, the colour distance, and the look-level range derivation.
"""

import json
import math

import pytest

from itp import size_match
from itp.size_match import color_distance, normalize_body, score_garment, score_look

# The reference body of the module docstring.
HEIGHT = 170.0
SHOULDER_RATIO = 0.223
WAIST_RATIO = 0.74
HIP_RATIO = 0.92
# girth / width for a section 0.75 as deep as it is wide (Ramanujan perimeter).
GIRTH_FACTOR = math.pi * (2.625 - math.sqrt(1.875 * 1.625))


def model(**overrides) -> dict:
    data = {
        "available": True,
        "shoulder_ratio": SHOULDER_RATIO,
        "waist_ratio": WAIST_RATIO,
        "hip_ratio": HIP_RATIO,
        "thickness_ratio": 0.19,
        "tags": {"build": "slim", "volume": "light", "legs": "long-leg"},
        "labels": {"build": "修长", "volume": "轻量", "legs": "长腿型", "pose": "T-Pose"},
    }
    data.update(overrides)
    return data


def body(**overrides) -> dict:
    data = {"height_cm": HEIGHT, "bust_cm": 88.0, "waist_cm": 70.0, "hip_cm": 92.0,
            "weight_kg": 60.0, "shoulder_cm": 38.0}
    data.update(overrides)
    return data


def garment(**overrides) -> dict:
    data = {
        "id": "g-knit",
        "category": "上装",
        "name": "细罗纹高领针织",
        "measurements": {"bust_cm": 104.0, "shoulder_cm": 40.0, "length_cm": 62.0},
        "fit_ranges": {"bust_cm": [88, 100], "waist_cm": [70, 84], "shoulder_cm": [37, 41]},
        "attributes": {"silhouette": "标准", "stretch": "微弹", "weight_gsm": 260,
                       "color": "#3c4a52", "length_type": "常规"},
        "style": "通勤",
        "season": "四季",
        "occasion": "通勤办公",
    }
    data.update(overrides)
    return data


def dimension(result: dict, key: str) -> dict:
    for record in result["dimensions"]:
        if record["key"] == key:
            return record
    raise AssertionError(f"{key} was not scored")


# --------------------------------------------------------------------------- normalise


def test_input_values_are_reported_as_input():
    profile = normalize_body(body())
    for key in ("height_cm", "weight_kg", "bust_cm", "shoulder_cm", "waist_cm", "hip_cm"):
        assert profile[key]["source"] == "input"
        assert profile[key]["confidence"] == size_match.INPUT_CONFIDENCE


def test_missing_everything_reports_missing_entries():
    profile = normalize_body(None)
    assert set(profile) == set(size_match.DIMENSION_ORDER)
    for entry in profile.values():
        assert entry == {"value": None, "source": "missing", "confidence": 0.0}


def test_shoulder_is_estimated_from_the_stature_ratio():
    profile = normalize_body({"height_cm": HEIGHT, "model": model()})
    assert profile["shoulder_cm"]["source"] == "estimated"
    assert profile["shoulder_cm"]["value"] == pytest.approx(HEIGHT * SHOULDER_RATIO, abs=0.05)
    assert profile["shoulder_cm"]["confidence"] == size_match.ESTIMATED_CONFIDENCE


def test_waist_and_hip_are_estimated_as_girths_not_widths():
    profile = normalize_body({"height_cm": HEIGHT, "model": model()})
    shoulder_width = HEIGHT * SHOULDER_RATIO
    assert profile["waist_cm"]["value"] == pytest.approx(
        GIRTH_FACTOR * shoulder_width * WAIST_RATIO, abs=0.1
    )
    assert profile["hip_cm"]["value"] == pytest.approx(
        GIRTH_FACTOR * shoulder_width * HIP_RATIO, abs=0.1
    )
    # A raw width would be far too small to sit in any garment girth window.
    assert profile["hip_cm"]["value"] > 90
    assert profile["waist_cm"]["value"] > 70


def test_derived_dimensions_carry_the_lowest_confidence():
    profile = normalize_body({"height_cm": HEIGHT, "model": model()})
    assert profile["waist_cm"]["confidence"] == size_match.ESTIMATED_CONVERTED_CONFIDENCE
    assert profile["hip_cm"]["confidence"] == size_match.ESTIMATED_CONVERTED_CONFIDENCE
    assert profile["shoulder_cm"]["confidence"] > profile["waist_cm"]["confidence"]


def test_bust_and_weight_are_never_estimated():
    profile = normalize_body({"height_cm": HEIGHT, "model": model()})
    assert profile["bust_cm"]["source"] == "missing"
    assert profile["weight_kg"]["source"] == "missing"


def test_input_beats_an_estimate_even_when_the_model_is_available():
    profile = normalize_body({"height_cm": HEIGHT, "waist_cm": 68.0, "model": model()})
    assert profile["waist_cm"] == {"value": 68.0, "source": "input", "confidence": 1.0}


def test_an_unavailable_model_estimates_nothing():
    profile = normalize_body({"height_cm": HEIGHT, "model": model(available=False)})
    assert profile["shoulder_cm"]["source"] == "missing"
    assert profile["waist_cm"]["source"] == "missing"


def test_an_implausible_estimate_drops_to_the_low_confidence():
    profile = normalize_body({"height_cm": HEIGHT, "model": model(shoulder_ratio=0.60)})
    entry = profile["shoulder_cm"]
    assert entry["source"] == "estimated"
    assert entry["value"] == pytest.approx(102.0, abs=0.1)
    assert entry["confidence"] == size_match.ESTIMATED_INPUT_CONFIDENCE


def test_malformed_body_input_is_ignored_rather_than_raising():
    profile = normalize_body({"height_cm": "tall", "bust_cm": None, "waist_cm": True,
                              "model": "not-a-dict"})
    for entry in profile.values():
        assert entry["source"] == "missing"


# --------------------------------------------------------------------------- fit curve


def test_centre_of_the_window_scores_the_maximum():
    result = score_garment(body(bust_cm=94.0), garment())
    record = dimension(result, "bust_cm")
    assert record["state"] == "fit"
    assert record["score"] == pytest.approx(size_match.FIT_CENTRE_SCORE)


def test_window_edges_score_the_edge_value():
    for value in (88.0, 100.0):
        record = dimension(score_garment(body(bust_cm=value), garment()), "bust_cm")
        assert record["state"] == "fit"
        assert record["score"] == pytest.approx(size_match.FIT_EDGE_SCORE, abs=0.001)


def test_the_score_rises_towards_the_centre_inside_the_window():
    scores = [dimension(score_garment(body(bust_cm=value), garment()), "bust_cm")["score"]
              for value in (88.0, 91.0, 94.0)]
    assert scores == sorted(scores)


def test_below_the_window_is_tight_and_above_is_loose():
    tight = dimension(score_garment(body(bust_cm=84.0), garment()), "bust_cm")
    loose = dimension(score_garment(body(bust_cm=106.0), garment()), "bust_cm")
    assert tight["state"] == "tight" and tight["delta_cm"] == pytest.approx(-4.0)
    assert loose["state"] == "loose" and loose["delta_cm"] == pytest.approx(6.0)
    assert "偏紧" in tight["detail"] and "偏松" in loose["detail"]


def test_out_of_range_decay_uses_the_wider_of_floor_and_half_width():
    # bust window is 12 wide, so the tolerance is max(2, 6) = 6cm.
    half = dimension(score_garment(body(bust_cm=103.0), garment()), "bust_cm")
    edge = dimension(score_garment(body(bust_cm=106.0), garment()), "bust_cm")
    assert half["score"] == pytest.approx(size_match.FIT_EDGE_SCORE / 2, abs=0.001)
    assert edge["score"] == pytest.approx(0.0, abs=0.001)
    # waist window is 14 wide: tolerance 7cm, so 7cm out still scores zero.
    waist = dimension(score_garment(body(waist_cm=91.0), garment()), "waist_cm")
    assert waist["score"] == pytest.approx(0.0, abs=0.001)


def test_a_narrow_window_uses_the_two_centimetre_floor():
    narrow = garment(fit_ranges={"bust_cm": [88, 90]})
    record = dimension(score_garment(body(bust_cm=92.0), narrow), "bust_cm")
    assert record["score"] == pytest.approx(0.0, abs=0.001)


def test_a_zero_width_window_matches_exactly():
    exact = garment(fit_ranges={"bust_cm": [90, 90]})
    record = dimension(score_garment(body(bust_cm=90.0), exact), "bust_cm")
    assert record["score"] == pytest.approx(size_match.FIT_CENTRE_SCORE)
    assert record["state"] == "fit"


def test_the_out_of_range_curve_is_continuous_at_the_boundary():
    just_out = dimension(score_garment(body(bust_cm=100.01), garment()), "bust_cm")
    assert just_out["score"] == pytest.approx(size_match.FIT_EDGE_SCORE, abs=0.01)


def test_malformed_ranges_are_treated_as_unknown():
    broken = garment(fit_ranges={"bust_cm": [100, 88], "waist_cm": "70-84",
                                 "hip_cm": [90], "height_cm": [None, 175]})
    result = score_garment(body(), broken)
    assert result["dimensions"] == []
    assert result["confidence"] == 0.0
    assert result["score"] == pytest.approx(size_match.NEUTRAL_SCORE)
    assert any("未提供任何适配区间" in warning for warning in result["warnings"])


# --------------------------------------------------------------------------- weights


def test_weights_are_renormalised_over_the_scored_dimensions():
    result = score_garment(body(), garment(fit_ranges={"bust_cm": [88, 100],
                                                       "shoulder_cm": [37, 41]}))
    total = sum(record["weight"] for record in result["dimensions"])
    assert total == pytest.approx(1.0, abs=0.001)
    bust = dimension(result, "bust_cm")
    shoulder = dimension(result, "shoulder_cm")
    assert bust["weight"] > shoulder["weight"]


def test_a_missing_dimension_does_not_lower_the_score():
    # Same body and the same bust window; the second garment also constrains the
    # waist, which the body cannot answer.
    plain = score_garment(body(waist_cm=None), garment(fit_ranges={"bust_cm": [88, 100]}))
    extra = score_garment(
        body(waist_cm=None),
        garment(fit_ranges={"bust_cm": [88, 100], "waist_cm": [70, 84]}),
    )
    assert extra["fit_score"] == pytest.approx(plain["fit_score"])
    assert extra["confidence"] < plain["confidence"]
    unknown = dimension(extra, "waist_cm")
    assert unknown["state"] == "unknown" and unknown["score"] is None
    assert unknown["body_source"] == "missing"


def test_unlisted_dimensions_get_the_default_weight():
    result = score_garment(body(), garment(fit_ranges={"bust_cm": [88, 100],
                                                       "calf_cm": [30, 40]}))
    calf = dimension(result, "calf_cm")
    assert calf["state"] == "unknown"  # no body value for it
    assert size_match.DEFAULT_DIMENSION_WEIGHT < size_match.DIMENSION_WEIGHTS["bust_cm"]


# --------------------------------------------------------------------------- confidence


def test_confidence_rises_as_more_measurements_arrive():
    weak = score_garment({"model": model()},
                         garment(fit_ranges={"bust_cm": [88, 100], "waist_cm": [70, 84]}))
    stronger = score_garment({"height_cm": HEIGHT, "bust_cm": 94.0, "model": model()},
                             garment(fit_ranges={"bust_cm": [88, 100],
                                                 "waist_cm": [70, 84]}))
    full = score_garment(body(bust_cm=94.0), garment(fit_ranges={"bust_cm": [88, 100],
                                                                 "waist_cm": [70, 84]}))
    assert weak["confidence"] < stronger["confidence"] < full["confidence"]
    assert full["confidence"] == pytest.approx(1.0)


def test_the_contraction_moves_an_unknown_score_towards_neutral():
    # No body data at all: plenty of windows, no answers, so the score must sit
    # close to the neutral value instead of claiming a confident 0.95.
    result = score_garment(None, garment())
    assert result["confidence"] == 0.0
    assert result["score"] == pytest.approx(size_match.NEUTRAL_SCORE)
    assert result["fit_score"] == pytest.approx(size_match.NEUTRAL_SCORE)


def test_the_contraction_is_monotone_in_confidence():
    high = score_garment(body(bust_cm=94.0, waist_cm=77.0), garment())
    low = score_garment({"height_cm": HEIGHT, "bust_cm": 94.0, "model": model()}, garment())
    assert high["fit_score"] >= low["fit_score"]
    assert high["score"] > low["score"]


# --------------------------------------------------------------------------- monotonicity


def test_a_better_fitting_garment_never_scores_lower():
    snug = garment(id="snug", fit_ranges={"bust_cm": [90, 98]})
    loose = garment(id="loose", fit_ranges={"bust_cm": [120, 130]})
    body_data = body(bust_cm=94.0)
    assert score_garment(body_data, snug)["score"] > score_garment(body_data, loose)["score"]


def test_moving_towards_the_window_never_lowers_the_score():
    window = garment(fit_ranges={"bust_cm": [88, 100]})
    scores = [score_garment(body(bust_cm=value), window)["score"]
              for value in (70.0, 80.0, 88.0, 94.0)]
    assert scores == sorted(scores)


# --------------------------------------------------------------------------- preferences


def test_preferences_are_absent_from_the_blend_when_none_are_given():
    result = score_garment(body(bust_cm=94.0, waist_cm=77.0, shoulder_cm=39.0),
                           garment(attributes={}))
    assert result["preference_score"] == pytest.approx(size_match.NEUTRAL_SCORE)
    assert result["score"] == pytest.approx(result["fit_score"], abs=0.02)


def test_a_matching_preference_raises_the_preference_score():
    base = garment(attributes={"silhouette": "宽松", "stretch": "无弹"})
    plain = score_garment(body(), base)
    matched = score_garment(body(), base, preferences={"silhouette": ["宽松"]})
    assert matched["preference_score"] > plain["preference_score"]
    assert matched["score"] > plain["score"]


def test_a_conflicting_preference_lowers_the_preference_score():
    result = score_garment(body(), garment(attributes={"silhouette": "修身"}),
                           preferences={"silhouette": ["宽松", "oversize"]})
    assert result["preference_score"] < size_match.NEUTRAL_SCORE
    assert any("不在你偏好的" in reason for reason in result["reasons"])


def test_weight_and_stretch_preferences_are_applied():
    target = garment(attributes={"stretch": "无弹", "weight_gsm": 500})
    result = score_garment(body(), target,
                           preferences={"stretch": ["微弹"], "weight_gsm": [200, 400]})
    assert result["preference_score"] < size_match.NEUTRAL_SCORE
    joined = " ".join(result["reasons"])
    assert "弹力" in joined and "克重" in joined


def test_colour_distance_is_a_cie76_delta_e():
    assert color_distance("#3c4a52", "#3c4a52") == pytest.approx(0.0)
    assert color_distance("#000000", "#ffffff") == pytest.approx(100.0, abs=1.0)
    assert color_distance("nope", "#ffffff") is None
    assert color_distance("#fff", "#ffffff") == pytest.approx(0.0)


def test_a_near_preferred_colour_is_rewarded_with_the_measured_delta_e():
    result = score_garment(body(), garment(attributes={"color": "#3c4a52"}),
                           preferences={"colors": ["#3d4b53"]})
    assert result["preference_score"] > size_match.NEUTRAL_SCORE
    assert any("ΔE" in reason for reason in result["reasons"])


def test_an_avoided_colour_is_penalised():
    avoided = score_garment(body(), garment(attributes={"color": "#3c4a52"}),
                            preferences={"avoid_colors": ["#3d4b53"]})
    assert avoided["preference_score"] < size_match.NEUTRAL_SCORE
    assert any("避开" in reason for reason in avoided["reasons"])


# --------------------------------------------------------------------------- tag rules


@pytest.mark.parametrize(("tags", "attributes", "expected"), [
    ({"build": "broad"}, {"silhouette": "宽松"}, "肩背宽阔，宽松或 oversize 的肩线不会绷住"),
    ({"build": "broad"}, {"silhouette": "修身"}, "肩背宽阔，修身版型容易在肩胛处拉扯"),
    ({"build": "slim"}, {"silhouette": "修身"}, "骨架修长，修身或标准版型更贴合"),
    ({"build": "slim"}, {"silhouette": "oversize"}, "骨架修长，oversize 容易被衣服本身撑大轮廓"),
    ({"volume": "heavy"}, {"silhouette": "oversize"}, "体量厚实，宽松廓形穿起来更舒展"),
    ({"volume": "light"}, {"silhouette": "宽松"}, "体量轻，宽松版型反而显得空"),
    ({"legs": "long-leg"}, {"length_type": "长款"}, "腿身比偏长，长款会压掉这部分优势"),
    ({"legs": "short-leg"}, {"length_type": "短款"}, "腿身比偏短，短款能抬高腰线"),
    ({"legs": "short-leg"}, {"length_type": "长款"}, "腿身比偏短，长款会进一步压低视觉重心"),
    ({"volume": "heavy"}, {"silhouette": "修身", "stretch": "无弹"},
     "体量厚实且面料无弹，修身版型活动受限"),
])
def test_every_body_tag_rule_produces_its_reason(tags, attributes, expected):
    body_data = {"height_cm": HEIGHT, "model": model(tags=tags, labels={})}
    result = score_garment(body_data, garment(attributes=attributes))
    assert expected in " ".join(result["reasons"])


def test_a_heavy_body_prefers_a_loose_cut_over_a_tight_one():
    body_data = {"height_cm": HEIGHT, "model": model(tags={"volume": "heavy"}, labels={})}
    loose = score_garment(body_data, garment(attributes={"silhouette": "宽松"}))
    tight = score_garment(body_data, garment(attributes={"silhouette": "修身",
                                                        "stretch": "无弹"}))
    assert loose["preference_score"] > tight["preference_score"]


def test_unknown_tags_leave_the_preference_component_neutral():
    result = score_garment(body(shoulder_cm=None, waist_cm=None, hip_cm=None),
                           garment(attributes={"silhouette": "宽松"}))
    assert result["preference_score"] == pytest.approx(size_match.NEUTRAL_SCORE)


# --------------------------------------------------------------------------- output shape


def test_reasons_are_chinese_numbered_and_bounded():
    result = score_garment(body(), garment())
    reasons = result["reasons"]
    assert size_match.MIN_REASONS <= len(reasons) <= size_match.MAX_REASONS
    assert any(any(char.isdigit() for char in reason) for reason in reasons)
    assert all(reason.strip() for reason in reasons)


def test_reasons_are_ordered_by_their_impact_on_the_score():
    result = score_garment(body(bust_cm=88.0, waist_cm=77.0), garment())
    impacts = {}
    for record in result["dimensions"]:
        if record["score"] is None:
            continue
        impacts[record["detail"]] = abs(record["weight"] * (record["score"] - 0.5))
    expected_first = max(impacts, key=lambda text: impacts[text])
    assert result["reasons"][0] == expected_first


def test_dimension_records_carry_the_documented_fields():
    record = dimension(score_garment(body(), garment()), "bust_cm")
    assert set(record) == {
        "key", "label", "weight", "score", "state", "body_value", "body_source",
        "range", "detail", "delta_cm",
    }
    assert record["label"] == "胸围"
    assert record["body_value"] == 88.0
    assert record["body_source"] == "input"
    assert record["range"] == [88.0, 100.0]


def test_tight_dimensions_generate_a_warning_naming_the_overshoot():
    result = score_garment(body(bust_cm=86.0), garment())
    assert any("胸围偏紧 2.0cm" in warning for warning in result["warnings"])
    assert any("建议选大一码" in warning for warning in result["warnings"])
    assert any("大一码" in suggestion for suggestion in result["suggestions"])


def test_estimated_dimensions_are_warned_about_including_the_conversion():
    result = score_garment({"height_cm": HEIGHT, "model": model()}, garment())
    joined = " ".join(result["warnings"])
    assert "肩宽" in joined and "模型估算值" in joined
    assert "腰围" in joined and "轮廓宽度换算" in joined


def test_an_empty_garment_is_scored_without_raising():
    result = score_garment(body(), {})
    assert result["dimensions"] == []
    assert result["score"] == pytest.approx(size_match.NEUTRAL_SCORE)
    assert result["confidence"] == 0.0
    assert result["reasons"] and result["warnings"]
    assert "该款未提供任何适配区间" in result["warnings"][0]


def test_a_missing_body_leaves_every_dimension_unknown():
    result = score_garment(None, garment())
    assert all(record["state"] == "unknown" for record in result["dimensions"])
    assert all(record["score"] is None for record in result["dimensions"])
    assert all(record["weight"] == 0.0 for record in result["dimensions"])
    assert result["confidence"] == 0.0


def test_scoring_is_deterministic():
    first = score_garment(body(), garment(), preferences={"silhouette": ["标准"]})
    second = score_garment(body(), garment(), preferences={"silhouette": ["标准"]})
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )


# --------------------------------------------------------------------------- looks


def look_members() -> list[dict]:
    return [
        garment(id="outer", category="外套", name="落肩薄呢外套",
                fit_ranges={"bust_cm": [92, 108], "shoulder_cm": [38, 44]}),
        garment(id="top", category="上装", name="细罗纹高领针织",
                fit_ranges={"bust_cm": [88, 100], "waist_cm": [70, 84]}),
        garment(id="bottom", category="下装", name="高腰直筒西裤",
                fit_ranges={"waist_cm": [68, 80], "hip_cm": [90, 102]}),
        garment(id="shoe", category="鞋履", name="方头乐福鞋", fit_ranges={"length_cm": [23, 25]}),
    ]


def test_a_look_blends_members_by_category_weight():
    members = look_members()
    result = score_look(body(shoulder_cm=40.0), {"id": "look-1", "garment_ids": ["top", "shoe"]},
                        members)
    scores = {member["garment_id"]: member["score"] for member in result["members"]}
    assert set(scores) == {"top", "shoe"}
    weights = size_match.CATEGORY_WEIGHTS
    expected = (weights["上装"] * scores["top"] + weights["鞋履"] * scores["shoe"]) / (
        weights["上装"] + weights["鞋履"]
    )
    assert result["score"] == pytest.approx(expected, abs=0.01)


def test_a_look_derives_each_dimension_from_the_member_intersection():
    members = look_members()
    result = score_look(body(shoulder_cm=40.0), {"garment_ids": ["outer", "top"]}, members)
    bust = dimension(result, "bust_cm")
    shoulder = dimension(result, "shoulder_cm")
    # outer is [92,108] and top is [88,100], so the intersection is [92,100].
    assert bust["range"] == [92.0, 100.0]
    assert shoulder["range"] == [38.0, 44.0]


def test_disjoint_member_windows_fall_back_and_warn():
    members = [
        garment(id="a", category="上装", fit_ranges={"waist_cm": [60, 70]}),
        garment(id="b", category="下装", fit_ranges={"waist_cm": [85, 95]}),
    ]
    result = score_look(body(), {"garment_ids": ["a", "b"]}, members)
    waist = dimension(result, "waist_cm")
    assert waist["range"] == [85.0, 95.0]
    assert any("适配区间不一致" in warning for warning in result["warnings"])


def test_missing_categories_are_not_penalised():
    members = look_members()
    both = score_look(body(shoulder_cm=40.0), {"garment_ids": ["top", "bottom"]}, members)
    one = score_look(body(shoulder_cm=40.0), {"garment_ids": ["top"]}, members)
    top_only = score_garment(body(shoulder_cm=40.0), members[1])
    assert one["score"] == pytest.approx(top_only["score"], abs=0.02)
    assert both["members"] and one["members"]


def test_a_look_accepts_dict_members_and_falls_back_to_every_garment():
    members = look_members()
    from_dicts = score_look(body(), {"members": [{"garment_id": "top"}, {"id": "bottom"}]}, members)
    assert {member["garment_id"] for member in from_dicts["members"]} == {"top", "bottom"}
    everything = score_look(body(), {}, members)
    assert len(everything["members"]) == len(members)


def test_a_look_without_members_warns_and_stays_neutral():
    result = score_look(body(), {"id": "empty"}, [])
    assert result["members"] == []
    assert result["score"] == pytest.approx(size_match.NEUTRAL_SCORE)
    assert any("没有找到该套装的成员单品" in warning for warning in result["warnings"])


def test_look_confidence_is_a_member_weighted_average():
    members = look_members()
    result = score_look(body(shoulder_cm=40.0), {"garment_ids": ["top", "bottom"]}, members)
    weights = size_match.CATEGORY_WEIGHTS
    expected = (
        weights["上装"] * score_garment(body(shoulder_cm=40.0), members[1])["confidence"]
        + weights["下装"] * score_garment(body(shoulder_cm=40.0), members[2])["confidence"]
    ) / (weights["上装"] + weights["下装"])
    assert result["confidence"] == pytest.approx(expected, abs=0.01)


def test_look_scoring_is_deterministic():
    members = look_members()
    first = score_look(body(), {"garment_ids": ["top", "bottom"]}, members)
    second = score_look(body(), {"garment_ids": ["top", "bottom"]}, members)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )


def test_a_look_result_keeps_the_garment_result_shape():
    result = score_look(body(), {"garment_ids": ["top"]}, look_members())
    assert set(result) == {
        "score", "fit_score", "preference_score", "confidence", "dimensions",
        "reasons", "warnings", "suggestions", "members",
    }
    assert set(result["members"][0]) == {"garment_id", "category", "score"}
    assert size_match.MIN_REASONS <= len(result["reasons"]) <= size_match.MAX_REASONS

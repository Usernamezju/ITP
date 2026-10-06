"""Privacy bounds and useful behavior of hybrid ranking, not opaque scores."""

import pytest

from itp.hybrid_recommendation import (
    HISTORY_MAX_COUNT, RANKING_WEIGHTS, normalize_history_preferences, rank_candidate,
)


def candidate(**overrides):
    return {"id": "same-public-product", "style": "通勤", "season": "秋",
            "occasion": "通勤办公", **overrides}


def test_cold_start_keeps_original_size_score_and_has_no_exploration():
    result = rank_candidate(0.8, candidate())
    assert result["score"] == 0.8
    assert result["cold_start"]
    assert result["components"]["history"] == result["components"]["exploration"] == 0.5
    assert result["reasons"] == []


def test_all_explicit_contexts_are_explained_in_chinese():
    result = rank_candidate(0.6, candidate(), style="通勤", season="秋", occasion="通勤办公")
    assert result["score"] > 0.6
    assert result["reasons"] == ["通勤风格匹配", "秋季节匹配", "通勤办公场景匹配"]


def test_history_promotes_preferred_style_and_reports_why():
    history = normalize_history_preferences({"styles": {"通勤": 12, "运动": 1}})
    liked = rank_candidate(0.8, candidate(), history_preferences=history)
    other = rank_candidate(0.8, candidate(style="运动"), history_preferences=history)
    assert liked["score"] > other["score"]
    assert "近期更常查看通勤风" in liked["reasons"]


def test_category_preferences_work_for_multi_garment_looks():
    history = normalize_history_preferences({"categories": {"外套": 12}})
    coat = rank_candidate(0.7, candidate(), history_preferences=history, categories=["外套", "外套"])
    shoe = rank_candidate(0.7, candidate(), history_preferences=history, categories=["鞋履"])
    assert coat["score"] > shoe["score"]
    assert "近期更常查看外套品类" in coat["reasons"]


def test_one_view_is_weaker_evidence_than_a_repeated_preference():
    once = rank_candidate(0.7, candidate(), history_preferences={"styles": {"通勤": 1}})
    repeated = rank_candidate(0.7, candidate(), history_preferences={"styles": {"通勤": 12}})
    assert once["components"]["history"] < repeated["components"]["history"]


def test_saturated_history_cannot_override_a_clear_size_mismatch():
    history = normalize_history_preferences({"styles": {"通勤": HISTORY_MAX_COUNT}})
    poor_fit = rank_candidate(0.0, candidate(), history_preferences=history)
    good_fit = rank_candidate(0.99, candidate(style="运动"), history_preferences=history)
    assert poor_fit["score"] < good_fit["score"]
    maximum = (RANKING_WEIGHTS["history"] + RANKING_WEIGHTS["exploration"]) / RANKING_WEIGHTS["size"] / 2
    assert poor_fit["score"] <= maximum + 0.0001


def test_exploration_is_reproducible_and_does_not_use_user_ids_or_clock():
    first = {"styles": {"通勤": 5, "运动": 3}, "categories": {"上装": 4}}
    reordered = {"categories": {"上装": 4}, "styles": {"运动": 3, "通勤": 5}}
    assert rank_candidate(0.7, candidate(), history_preferences=first) == rank_candidate(
        0.7, candidate(), history_preferences=reordered)


@pytest.mark.parametrize("value", [
    {"events": []}, {"user_id": "private"}, {"styles": {"unknown": 1}},
    {"styles": {"通勤": -1}}, {"styles": {"通勤": 21}}, {"styles": {"通勤": True}},
    {"styles": {"通勤": 1.5}}, {"styles": None}, ["通勤"],
])
def test_compact_vector_rejects_raw_logs_and_unbounded_or_invalid_counts(value):
    with pytest.raises(ValueError):
        normalize_history_preferences(value)


def test_empty_counts_remain_cold_start():
    history = normalize_history_preferences({"styles": {"通勤": 0}, "categories": {}})
    assert history == {}
    assert rank_candidate(0.5, candidate(), history_preferences=history)["cold_start"]


def test_catalogue_context_is_not_applied_twice():
    result = rank_candidate(0.4, candidate(), style="通勤", context_in_base=True)
    assert result["score"] == 0.4
    assert result["components"]["style"] == 1.0

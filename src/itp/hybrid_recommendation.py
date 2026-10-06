"""Bounded, explainable ranking above the unchanged size/catalogue scorers.

Only compact public style/category counts enter this module. It does not store
customer measurements, events, identifiers or a recommendation profile.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from itp.garments import CATEGORIES, STYLES

# Relative weights, centred around neutral so cold start preserves the base.
RANKING_WEIGHTS = {
    "size": 0.72,
    "style": 0.08,
    "season": 0.05,
    "occasion": 0.05,
    "history": 0.08,
    "exploration": 0.02,
}
HISTORY_FAMILIES = {"styles": tuple(STYLES), "categories": tuple(CATEGORIES)}
HISTORY_MAX_COUNT = 20
HISTORY_RELIABILITY_COUNT = 8
NEUTRAL = 0.5


def normalize_history_preferences(value: Any) -> dict[str, dict[str, int]]:
    """Validate a small count vector, excluding raw events and customer ids."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(HISTORY_FAMILIES):
        raise ValueError("历史偏好只接受风格和品类计数，不接受个人资料或浏览日志")
    result: dict[str, dict[str, int]] = {}
    for family, allowed in HISTORY_FAMILIES.items():
        counts = value.get(family, {})
        if not isinstance(counts, dict) or set(counts) - set(allowed):
            raise ValueError("历史偏好包含未知风格或品类")
        cleaned: dict[str, int] = {}
        for key, count in counts.items():
            if type(count) is not int or not 0 <= count <= HISTORY_MAX_COUNT:
                raise ValueError(f"历史偏好计数须为 0–{HISTORY_MAX_COUNT} 的整数")
            if count:
                cleaned[key] = count
        if cleaned:
            result[family] = cleaned
    return result


def _history_signal(counts: dict[str, int], values: list[str], family: str) -> float:
    """Smoothed preference: weak evidence stays neutral; saturation is bounded."""
    if not counts or not values:
        return NEUTRAL
    total = sum(counts.values())
    baseline = 1 / len(HISTORY_FAMILIES[family])
    reliability = min(1.0, total / HISTORY_RELIABILITY_COUNT)
    signals = []
    for value in values:
        if value not in HISTORY_FAMILIES[family]:
            continue
        share = counts.get(value, 0) / total
        centred = (share - baseline) / (1 - baseline if share >= baseline else baseline)
        signals.append(NEUTRAL + NEUTRAL * centred * reliability)
    return sum(signals) / len(signals) if signals else NEUTRAL


def rank_candidate(
    base_score: float,
    candidate: dict[str, Any],
    *,
    style: str | None = None,
    season: str | None = None,
    occasion: str | None = None,
    history_preferences: dict[str, dict[str, int]] | None = None,
    categories: list[str] | None = None,
    context_in_base: bool = False,
) -> dict[str, Any]:
    """Return ranking evidence without modifying the underlying fit report.

    score = clamp(base + sum(w[k] / w[size] * (signal[k] - 0.5))).
    Unspecified context is neutral; history and exploration are neutral during
    cold start. Explicit merchant filters remain enforced by outfit_service.
    """
    history = history_preferences or {}
    components = {"size": base_score, **{
        key: NEUTRAL for key in RANKING_WEIGHTS if key != "size"
    }}
    reasons: list[str] = []
    for key, wanted, label in (
        ("style", style, "风格"), ("season", season, "季节"), ("occasion", occasion, "场景"),
    ):
        if not wanted:
            continue
        supplied = candidate.get(key)
        if supplied:
            matched = supplied == wanted
            components[key] = 1.0 if matched else 0.0
            if matched:
                reasons.append(f"{wanted}{label}匹配")

    if history:
        signals = []
        if history.get("styles") and candidate.get("style"):
            signal = _history_signal(history["styles"], [candidate["style"]], "styles")
            signals.append(signal)
            if signal > NEUTRAL:
                reasons.append(f"近期更常查看{candidate['style']}风")
        if history.get("categories") and categories:
            known = list(dict.fromkeys(category for category in categories if category in CATEGORIES))
            if known:
                signal = _history_signal(history["categories"], known, "categories")
                signals.append(signal)
                if signal > NEUTRAL:
                    reasons.append(f"近期更常查看{'、'.join(known[:2])}品类")
        components["history"] = sum(signals) / len(signals) if signals else NEUTRAL
        # Deterministic exploration changes with the compact vector, never with
        # a user identity or the clock. Repeat requests remain reproducible.
        seed = str(candidate.get("id", "")) + json.dumps(history, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(seed.encode()).digest()
        components["exploration"] = int.from_bytes(digest[:4], "big") / (2**32 - 1)
        if components["exploration"] > 0.8:
            reasons.append("兼顾不同款式，给你一些新选择")

    adjustment = sum(
        weight / RANKING_WEIGHTS["size"] * (components[key] - NEUTRAL)
        for key, weight in RANKING_WEIGHTS.items()
        if key != "size" and not (context_in_base and key in {"style", "season", "occasion"})
    )
    score = max(0.0, min(1.0, base_score + adjustment))
    return {
        "score": round(score, 4),
        "base_score": base_score,
        "components": {key: round(value, 4) for key, value in components.items()},
        "weights": dict(RANKING_WEIGHTS),
        "reasons": reasons,
        "cold_start": not bool(history),
        "context_in_base": context_in_base,
    }

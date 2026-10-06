"""Explainable size matching between a body profile and a garment catalogue.

Everything here is a pure function: no I/O, no clock, no randomness, no other
``itp`` import.  The same input always produces the same output, and every
number in the result can be traced back to one body dimension, one garment
attribute or one stated preference.

Inputs
------
``body`` (every field optional)::

    {"height_cm": 170.0, "weight_kg": 60.0, "shoulder_cm": 38.0,
     "bust_cm": 88.0, "waist_cm": 70.0, "hip_cm": 92.0,
     "model": {"available": True, "shoulder_ratio": 0.223, "waist_ratio": 0.74,
               "hip_ratio": 0.92, "leg_ratio": 0.47, "thickness_ratio": 0.19,
               "tags": {"build": "slim", "volume": "light", "legs": "long-leg"},
               "labels": {"build": "修长", "volume": "轻量", "legs": "长腿型"}}}

``garment``::

    {"id": "...", "category": "上装", "name": "...",
     "measurements": {...}, "fit_ranges": {"bust_cm": [88, 100], ...},
     "attributes": {"silhouette": "标准", "stretch": "微弹", "weight_gsm": 260,
                    "color": "#3c4a52", "length_type": "常规"},
     "style": "通勤", "season": "四季", "occasion": "通勤办公"}

Any key may be missing or ``None``.

Body dimensions
---------------
``normalize_body`` reports one entry per dimension as
``{"value", "source", "confidence"}`` with ``source`` in ``input`` /
``estimated`` / ``missing``.  A user value is always preferred; when it is
absent and the GLB model carries the matching ratio, the value is derived from
the stature.

**Units matter here.** The model's ratios come from the silhouette analysis, so
``shoulder_ratio`` is a *front-view width* over the stature and ``waist_ratio``
/ ``hip_ratio`` are *widths* relative to that shoulder width.  A garment's
``shoulder_cm`` window is also a width, but its ``bust_cm`` / ``waist_cm`` /
``hip_cm`` windows are *girths* (a tape-measure measurement).  Comparing a width
to a girth window would report every garment as far too tight, so widths are
converted to girths with Ramanujan's ellipse perimeter, taking the torso
section as ``TORSO_DEPTH_TO_WIDTH`` (0.75) as deep as it is wide::

    a = width / 2, b = a * TORSO_DEPTH_TO_WIDTH
    girth = pi * (3 * (a + b) - sqrt((3 * a + b) * (a + 3 * b)))

For the reference body (stature 170cm, shoulder ratio 0.223, waist ratio 0.74,
hip ratio 0.92) that yields a shoulder width of 37.9cm, a waist girth of 77.4cm
and a hip girth of 96.4cm, which matches ordinary anthropometry for that
stature.  The model exposes no per-band depth, so this rests on one documented
constant instead of on the model's own numbers; converted dimensions therefore
carry the lowest confidence and an explicit warning.

==================  ==========================================  ================
dimension           estimate                                    confidence
==================  ==========================================  ================
``shoulder_cm``     ``height_cm * shoulder_ratio``               0.7
``waist_cm``        girth of ``shoulder_width * waist_ratio``    0.3
``hip_cm``          girth of ``shoulder_width * hip_ratio``      0.3
``bust_cm``         never estimated                              --
``height_cm``       never estimated                              --
``weight_kg``       never estimated                              --
==================  ==========================================  ================

The shoulder width used by the waist and hip conversions is resolved first,
even when it is itself an estimate, so the chain ``height -> shoulder -> waist``
works from a single user value.  ``bust_cm`` cannot be estimated because the
model carries no bust ratio; it stays ``missing`` unless the user measured it.
Estimated values carry ``ESTIMATED_CONFIDENCE`` (0.7) when the derived number
falls inside ``SANITY_WINDOWS`` for that dimension and
``ESTIMATED_INPUT_CONFIDENCE`` (0.3) when it does not, so an implausible ratio
cannot masquerade as a solid measurement.  Missing dimensions report
``value = None`` and confidence 0.0.

Dimension weights
-----------------
Bust and shoulder dominate, waist and hip follow, height is medium and anything
else the garment provides is low::

    DIMENSION_WEIGHTS = {"bust_cm": 0.28, "shoulder_cm": 0.24, "waist_cm": 0.18,
                         "hip_cm": 0.16, "height_cm": 0.14}
    DEFAULT_DIMENSION_WEIGHT = 0.08

Weights are renormalised over the dimensions that can actually be scored, so a
missing measurement never lowers the score; it lowers ``confidence`` instead.
Each reported dimension carries its renormalised weight, and the weights of the
scored dimensions sum to 1.

In-range score
--------------
With ``centre = (min + max) / 2`` and ``half = (max - min) / 2``::

    score = FIT_CENTRE_SCORE - (FIT_CENTRE_SCORE - FIT_EDGE_SCORE) * |value - centre| / half

so the centre of the window scores ``FIT_CENTRE_SCORE`` (1.0) and both edges
score ``FIT_EDGE_SCORE`` (0.85); ``state = "fit"`` and ``delta_cm = 0.0``.  A
zero-width window scores ``FIT_CENTRE_SCORE`` when the value matches exactly.

Out-of-range score
------------------
``delta_cm`` is signed: negative below the window, positive above it.  The
tolerance scale is the wider of a fixed floor and half the window::

    tolerance = max(TOLERANCE_MIN_CM, (max - min) * TOLERANCE_WIDTH_FACTOR)   # 2.0cm, 0.5
    score     = max(0.0, FIT_EDGE_SCORE * (1 - |delta_cm| / tolerance))

The decay starts at the edge value (0.85 at ``delta_cm = 0``) and reaches 0 one
tolerance away, so the curve is continuous at the window boundary.  ``state`` is
``"tight"`` below the window and ``"loose"`` above it.  A dimension whose window
is missing, or whose body value is missing, reports ``state = "unknown"``,
``score = None`` and ``delta_cm = None``, and is excluded from the fit score.

Preference and consistency score
--------------------------------
``preference_score`` starts at ``NEUTRAL_SCORE`` (0.5) and each signal moves it;
the result is clamped to 0..1.  Positive signals are scaled so a full match
saturates at 1.0.

User preferences (``preferences``, all optional)::

    silhouette     ["标准", "宽松"]        match +0.25   conflict -0.15
    stretch        ["微弹"]                match +0.15   conflict -0.10
    weight_gsm     [200, 400]              inside +0.10  outside  -0.08
    colors         ["#3c4a52"]             near   +0.15  far      -0.12
    avoid_colors   ["#ff0000"]             near           -0.35

Colour distance is CIE76 ΔE in CIE Lab (D65): a colour is "near" at
``COLOR_NEAR_DELTA_E`` (12.0) or less and "far" at ``COLOR_FAR_DELTA_E`` (30.0)
or more, and the reason quotes the measured ΔE.

Body-tag consistency (from the model tags, applied whether or not the caller
stated a preference).  Each row is a reasoned, hand-written rule::

    tag                  garment attribute            delta
    build=broad          silhouette 宽松/oversize     +0.06
    build=broad          silhouette 修身              -0.08
    build=slim           silhouette 修身/标准         +0.04
    build=slim           silhouette oversize          -0.05
    volume=heavy         silhouette 宽松/oversize     +0.03
    volume=heavy         silhouette 修身 & stretch 无弹  -0.07
    volume=light         silhouette 宽松/oversize     -0.03
    legs=long-leg        length_type 长款             -0.03
    legs=short-leg       length_type 短款             +0.04
    legs=short-leg       length_type 长款             -0.02

Rows are mutually exclusive per (tag, attribute) pair; when several rows fire
their deltas are summed.

Total score
-----------
``fit_score`` is the weighted mean of the scored dimensions.  The preference
component enters as a **centred** adjustment, so the no-information case is
exactly the fit score and a satisfied preference can never drag the total down::

    PREFERENCE_WEIGHT = 0.30            # how far a fully matching preference can move the score
    raw    = fit_score + PREFERENCE_WEIGHT * (preference_score - NEUTRAL_SCORE)
    factor = SHRINK_FLOOR + (1 - SHRINK_FLOOR) * confidence          # SHRINK_FLOOR = 0.30
    score  = NEUTRAL_SCORE + (raw - NEUTRAL_SCORE) * factor

Equivalently the fit share is ``1 - PREFERENCE_WEIGHT`` and the preference share
is ``PREFERENCE_WEIGHT``, measured relative to neutral (0.5): with no preference
or attribute signal ``preference_score`` *is* neutral, so ``score`` follows
``fit_score`` directly and no special case is needed.  Written plainly, ``score``
is a weighted blend of the fit score and the preference score shifted so that
neutral means "no change"; a full preference match lifts the score by up to
0.15 and a total conflict lowers it by up to 0.12.

Confidence
----------
``confidence`` is the weight-weighted mean of the confidences of the dimensions
that the garment constrains *and* the body provides, times a penalty for the
constrained dimensions that the body cannot answer::

    confidence = mean_confidence * (1 - MISSING_PENALTY * missing_ratio)   # MISSING_PENALTY = 0.5

A garment that constrains nothing scores 0.0 confidence and a neutral fit.

Looks
-----
``score_look`` scores every member, blends them by category weight (outerwear
and tops matter most, shoes and accessories least)::

    CATEGORY_WEIGHTS = {"外套": 0.30, "上装": 0.28, "下装": 0.26, "鞋履": 0.08, "配饰": 0.08}

Categories absent from the look are simply not part of the blend.  The look's
``score`` is exactly that blend of the member scores, and each member score is
already contracted by its own confidence, so no second contraction is applied
here - that would punish the same missing measurement twice.  The look reports
the member-weighted ``confidence`` instead.  Its own ``dimensions`` come from
ranges **derived per dimension across its members**: the intersection when the
members agree, and otherwise the tightest lower bound with the loosest upper
bound, flagged by a warning.  Reasons are merged, de-duplicated and ordered by
impact.

Member references are read from ``look["garment_ids"]``, ``look["garments"]``,
``look["members"]`` (ids or dicts) or failing all of those, every garment passed
in, so the caller's look shape stays flexible.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["color_distance", "normalize_body", "score_garment", "score_look"]

# --- dimensions -----------------------------------------------------------------

DIMENSION_ORDER = ("bust_cm", "shoulder_cm", "waist_cm", "hip_cm", "height_cm", "weight_kg")
DIMENSION_LABELS = {
    "bust_cm": "胸围",
    "shoulder_cm": "肩宽",
    "waist_cm": "腰围",
    "hip_cm": "臀围",
    "height_cm": "身高",
    "weight_kg": "体重",
}

# Weights are renormalised over the scored dimensions, so they are relative.
DIMENSION_WEIGHTS = {
    "bust_cm": 0.28,
    "shoulder_cm": 0.24,
    "waist_cm": 0.18,
    "hip_cm": 0.16,
    "height_cm": 0.14,
}
DEFAULT_DIMENSION_WEIGHT = 0.08

# Plausibility windows for estimated dimensions, in centimetres / kilograms.
SANITY_WINDOWS = {
    "shoulder_cm": (28.0, 62.0),
    "bust_cm": (60.0, 150.0),
    "waist_cm": (45.0, 140.0),
    "hip_cm": (65.0, 160.0),
    "height_cm": (120.0, 230.0),
    "weight_kg": (30.0, 200.0),
}

INPUT_CONFIDENCE = 1.0
ESTIMATED_CONFIDENCE = 0.7
ESTIMATED_INPUT_CONFIDENCE = 0.3
MISSING_CONFIDENCE = 0.0
# Waist and hip are derived from a silhouette width through an ellipse
# approximation, which is an extra assumption on top of the model's own numbers.
ESTIMATED_CONVERTED_CONFIDENCE = 0.3
CONVERTED_DIMENSIONS = ("waist_cm", "hip_cm")
# Torso cross-section depth relative to its width, used by the girth conversion.
TORSO_DEPTH_TO_WIDTH = 0.75

# --- fit scoring ----------------------------------------------------------------

FIT_CENTRE_SCORE = 1.0
FIT_EDGE_SCORE = 0.85
TOLERANCE_MIN_CM = 2.0
TOLERANCE_WIDTH_FACTOR = 0.5

# --- blending -------------------------------------------------------------------

NEUTRAL_SCORE = 0.5
# How far a fully matching (or fully conflicting) preference may move the score.
# The fit share of the blend is the remainder, 1 - PREFERENCE_WEIGHT.
PREFERENCE_WEIGHT = 0.30
FIT_WEIGHT = 1.0 - PREFERENCE_WEIGHT
SHRINK_FLOOR = 0.30
MISSING_PENALTY = 0.5
MAX_REASONS = 5
MIN_REASONS = 2

# --- preferences ----------------------------------------------------------------

PREFERENCE_MATCH = {"silhouette": 0.25, "stretch": 0.15, "weight_gsm": 0.10, "color": 0.15}
PREFERENCE_CONFLICT = {"silhouette": -0.15, "stretch": -0.10, "weight_gsm": -0.08, "color": -0.12}
AVOID_COLOR_PENALTY = -0.35
COLOR_NEAR_DELTA_E = 12.0
COLOR_FAR_DELTA_E = 30.0

# --- body tag / garment attribute consistency -----------------------------------

TAG_RULES = (
    ("build", "broad", "silhouette", ("宽松", "oversize"), 0.06,
     "肩背宽阔，宽松或 oversize 的肩线不会绷住"),
    ("build", "broad", "silhouette", ("修身",), -0.08,
     "肩背宽阔，修身版型容易在肩胛处拉扯"),
    ("build", "slim", "silhouette", ("修身", "标准"), 0.04,
     "骨架修长，修身或标准版型更贴合"),
    ("build", "slim", "silhouette", ("oversize",), -0.05,
     "骨架修长，oversize 容易被衣服本身撑大轮廓"),
    ("volume", "heavy", "silhouette", ("宽松", "oversize"), 0.03,
     "体量厚实，宽松廓形穿起来更舒展"),
    ("volume", "light", "silhouette", ("宽松", "oversize"), -0.03,
     "体量轻，宽松版型反而显得空"),
    ("legs", "long-leg", "length_type", ("长款",), -0.03,
     "腿身比偏长，长款会压掉这部分优势"),
    ("legs", "short-leg", "length_type", ("短款",), 0.04,
     "腿身比偏短，短款能抬高腰线"),
    ("legs", "short-leg", "length_type", ("长款",), -0.02,
     "腿身比偏短，长款会进一步压低视觉重心"),
)
TAG_RULE_STRETCH = ("volume", "heavy", "无弹", "修身", -0.07,
                    "体量厚实且面料无弹，修身版型活动受限")

# --- looks ----------------------------------------------------------------------

CATEGORY_WEIGHTS = {"外套": 0.30, "上装": 0.28, "下装": 0.26, "鞋履": 0.08, "配饰": 0.08}
DEFAULT_CATEGORY_WEIGHT = 0.10


# --------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    """Return a finite float, or None for anything that is not a real number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _label(key: str) -> str:
    return DIMENSION_LABELS.get(key, key.replace("_cm", "").replace("_", ""))


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _range_of(container: Any, key: str) -> tuple[float, float] | None:
    """Read a ``[low, high]`` pair, rejecting anything malformed."""
    raw = _mapping(container).get(key)
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    low, high = _number(raw[0]), _number(raw[1])
    if low is None or high is None or low > high:
        return None
    return (low, high)


def _entry(value: float | None, source: str, confidence: float) -> dict:
    return {"value": _round(value, 1), "source": source, "confidence": round(confidence, 2)}


def _missing_entry() -> dict:
    return _entry(None, "missing", MISSING_CONFIDENCE)


def _input_entry(value: Any) -> dict | None:
    number = _number(value)
    return None if number is None else _entry(number, "input", INPUT_CONFIDENCE)


def _estimated_entry(
    key: str, value: float | None, *, available: bool, confidence: float | None = None
) -> dict | None:
    if not available or value is None or value <= 0:
        return None
    window = SANITY_WINDOWS.get(key)
    plausible = window is None or window[0] <= value <= window[1]
    if confidence is None:
        confidence = ESTIMATED_CONFIDENCE if plausible else ESTIMATED_INPUT_CONFIDENCE
    elif not plausible:
        confidence = min(confidence, ESTIMATED_INPUT_CONFIDENCE)
    return _entry(value, "estimated", confidence)


def _width_to_girth(width_cm: float) -> float:
    """Girth of an elliptical section ``TORSO_DEPTH_TO_WIDTH`` as deep as it is wide.

    Ramanujan's second perimeter approximation; used to turn the silhouette
    widths of the model into the tape-measure girths that garment windows use.
    """
    a = width_cm / 2
    b = a * TORSO_DEPTH_TO_WIDTH
    return math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))


def _model(body: Any) -> dict:
    return _mapping(_mapping(body).get("model"))


def _ratio(body: Any, key: str) -> float | None:
    model = _model(body)
    if not model.get("available"):
        return None
    value = _number(model.get(key))
    if value is None or value <= 0:
        return None
    return value


def _tags(body: Any) -> dict:
    return _mapping(_model(body).get("tags"))


def _ordered_dimensions(container: Any) -> list[str]:
    keys = [key for key in container if _range_of(container, key) is not None]
    ordered = [key for key in DIMENSION_ORDER if key in keys]
    return ordered + sorted(key for key in keys if key not in DIMENSION_ORDER)


# --------------------------------------------------------------------------------
# colour distance
# --------------------------------------------------------------------------------


def _hex_to_rgb(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def _rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    channels = []
    for raw in rgb:
        value = raw / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    x = (0.4124 * red + 0.3576 * green + 0.1805 * blue) / 0.95047
    y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    z = (0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883

    def curve(value: float) -> float:
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    fx, fy, fz = curve(x), curve(y), curve(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def color_distance(left: Any, right: Any) -> float | None:
    """CIE76 ΔE between two hex colours, or None when either is unreadable."""
    first, second = _hex_to_rgb(left), _hex_to_rgb(right)
    if first is None or second is None:
        return None
    lab_a, lab_b = _rgb_to_lab(first), _rgb_to_lab(second)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(lab_a, lab_b, strict=True)))


# --------------------------------------------------------------------------------
# body normalisation
# --------------------------------------------------------------------------------


def normalize_body(body: dict | None) -> dict:
    """Merge the user's measurements with the proportions of the 3D model.

    Returns one ``{"value", "source", "confidence"}`` entry per dimension in
    ``DIMENSION_ORDER``.  ``source`` is ``input`` for a user value, ``estimated``
    for a value derived from ``height_cm`` and a model ratio, and ``missing``
    when neither is available.  An estimated value is never reported as an input
    value, so the interface can label it.
    """
    data = _mapping(body)
    available = bool(_model(body).get("available"))

    height = _input_entry(data.get("height_cm"))
    weight = _input_entry(data.get("weight_kg"))
    bust = _input_entry(data.get("bust_cm"))

    shoulder = _input_entry(data.get("shoulder_cm"))
    if shoulder is None:
        ratio = _ratio(body, "shoulder_ratio")
        if height is not None and ratio is not None:
            shoulder = _estimated_entry("shoulder_cm", height["value"] * ratio, available=available)

    waist = _input_entry(data.get("waist_cm"))
    if waist is None and shoulder is not None:
        ratio = _ratio(body, "waist_ratio")
        if ratio is not None:
            waist = _estimated_entry(
                "waist_cm",
                _width_to_girth(shoulder["value"] * ratio),
                available=available,
                confidence=ESTIMATED_CONVERTED_CONFIDENCE,
            )

    hip = _input_entry(data.get("hip_cm"))
    if hip is None and shoulder is not None:
        ratio = _ratio(body, "hip_ratio")
        if ratio is not None:
            hip = _estimated_entry(
                "hip_cm",
                _width_to_girth(shoulder["value"] * ratio),
                available=available,
                confidence=ESTIMATED_CONVERTED_CONFIDENCE,
            )

    return {
        "height_cm": height or _missing_entry(),
        "weight_kg": weight or _missing_entry(),
        "bust_cm": bust or _missing_entry(),
        "shoulder_cm": shoulder or _missing_entry(),
        "waist_cm": waist or _missing_entry(),
        "hip_cm": hip or _missing_entry(),
    }


# --------------------------------------------------------------------------------
# one dimension
# --------------------------------------------------------------------------------


def _score_dimension(key: str, entry: dict, window: tuple[float, float] | None) -> dict:
    """Score one dimension, always returning the full explainable record."""
    label = _label(key)
    value = entry["value"]
    record = {
        "key": key,
        "label": label,
        "weight": 0.0,
        "score": None,
        "state": "unknown",
        "body_value": value,
        "body_source": entry["source"],
        "range": list(window) if window else None,
        "detail": "",
        "delta_cm": None,
    }
    if window is None:
        record["detail"] = f"该款未提供{label}的适配区间"
        return record
    low, high = window
    if value is None:
        record["detail"] = f"缺少{label}数据，无法判断是否落在 {low:g}-{high:g}cm 内"
        return record

    if low <= value <= high:
        half = (high - low) / 2
        if half <= 0:
            ratio = 0.0
        else:
            ratio = abs(value - (low + high) / 2) / half
        score = FIT_CENTRE_SCORE - (FIT_CENTRE_SCORE - FIT_EDGE_SCORE) * ratio
        record.update(score=round(_clamp(score), 4), state="fit", delta_cm=0.0)
        if half <= 0:
            record["detail"] = f"你的{label} {value:g}cm 正好等于该款适配值 {low:g}cm"
        else:
            record["detail"] = (
                f"你的{label} {value:g}cm 落在该款适配区间 {low:g}-{high:g}cm 内"
            )
        return record

    delta = value - high if value > high else value - low
    width = high - low
    tolerance = max(TOLERANCE_MIN_CM, width * TOLERANCE_WIDTH_FACTOR)
    score = max(0.0, FIT_EDGE_SCORE * (1 - abs(delta) / tolerance))
    state = "loose" if value > high else "tight"
    record.update(score=round(score, 4), state=state, delta_cm=round(delta, 2))
    direction = "偏松" if state == "loose" else "偏紧"
    record["detail"] = (
        f"你的{label} {value:g}cm 比该款适配区间 {low:g}-{high:g}cm "
        f"{direction} {abs(delta):.1f}cm"
    )
    return record


# --------------------------------------------------------------------------------
# preferences and consistency
# --------------------------------------------------------------------------------


def _attribute_signals(tags: dict, attributes: dict) -> list[dict]:
    """Apply the documented body-tag rules, newest first is irrelevant here."""
    signals: list[dict] = []
    for family, tag, attribute, accepted, delta, reason in TAG_RULES:
        if tags.get(family) != tag:
            continue
        if attributes.get(attribute) not in accepted:
            continue
        signals.append({"delta": delta, "text": reason, "kind": "attribute"})

    family, tag, stretch, silhouette, delta, reason = TAG_RULE_STRETCH
    if (
        tags.get(family) == tag
        and attributes.get("stretch") == stretch
        and attributes.get("silhouette") == silhouette
    ):
        signals.append({"delta": delta, "text": reason, "kind": "attribute"})
    return signals


def _preference_signals(attributes: dict, preferences: dict) -> list[dict]:
    """Score the stated preferences against one garment's attributes."""
    signals: list[dict] = []
    if not preferences:
        return signals

    wanted = preferences.get("silhouette")
    if isinstance(wanted, (list, tuple)) and wanted:
        silhouette = attributes.get("silhouette")
        if silhouette in wanted:
            signals.append({"delta": PREFERENCE_MATCH["silhouette"],
                            "text": f"版型 {silhouette} 命中你的偏好", "kind": "preference"})
        elif silhouette:
            signals.append({"delta": PREFERENCE_CONFLICT["silhouette"],
                            "text": f"版型 {silhouette} 不在你偏好的 "
                                    f"{'/'.join(str(item) for item in wanted)} 之内",
                            "kind": "preference"})

    wanted = preferences.get("stretch")
    if isinstance(wanted, (list, tuple)) and wanted:
        stretch = attributes.get("stretch")
        if stretch in wanted:
            signals.append({"delta": PREFERENCE_MATCH["stretch"],
                            "text": f"弹力 {stretch} 符合你的偏好", "kind": "preference"})
        elif stretch:
            signals.append({"delta": PREFERENCE_CONFLICT["stretch"],
                            "text": f"弹力 {stretch} 与你偏好的 "
                                    f"{'/'.join(str(item) for item in wanted)} 不同",
                            "kind": "preference"})

    window = preferences.get("weight_gsm")
    if isinstance(window, (list, tuple)) and len(window) == 2:
        low, high = _number(window[0]), _number(window[1])
        weight = _number(attributes.get("weight_gsm"))
        if low is not None and high is not None and weight is not None:
            if low <= weight <= high:
                signals.append({"delta": PREFERENCE_MATCH["weight_gsm"],
                                "text": f"克重 {weight:g}g/m² 在你偏好的 {low:g}-{high:g} 内",
                                "kind": "preference"})
            else:
                signals.append({"delta": PREFERENCE_CONFLICT["weight_gsm"],
                                "text": f"克重 {weight:g}g/m² 超出你偏好的 {low:g}-{high:g}",
                                "kind": "preference"})

    color = attributes.get("color")
    avoided = preferences.get("avoid_colors")
    if color and isinstance(avoided, (list, tuple)) and avoided:
        best = None
        for candidate in avoided:
            distance = color_distance(color, candidate)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, candidate)
        if best is not None and best[0] <= COLOR_NEAR_DELTA_E:
            signals.append({"delta": AVOID_COLOR_PENALTY,
                            "text": f"主色与你要避开的高相似（ΔE {best[0]:.1f}）",
                            "kind": "preference"})

    wanted_colors = preferences.get("colors")
    if color and isinstance(wanted_colors, (list, tuple)) and wanted_colors:
        best = None
        for candidate in wanted_colors:
            distance = color_distance(color, candidate)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, candidate)
        if best is not None:
            if best[0] <= COLOR_NEAR_DELTA_E:
                signals.append({"delta": PREFERENCE_MATCH["color"],
                                "text": f"主色与你的偏好色相近（ΔE {best[0]:.1f}）",
                                "kind": "preference"})
            elif best[0] >= COLOR_FAR_DELTA_E:
                signals.append({"delta": PREFERENCE_CONFLICT["color"],
                                "text": f"主色与你的偏好色差距较大（ΔE {best[0]:.1f}）",
                                "kind": "preference"})
    return signals


# --------------------------------------------------------------------------------
# garment scoring
# --------------------------------------------------------------------------------


def _confidence(profile: dict, ranges: dict, scored: list[str], constrained: list[str]) -> float:
    """Confidence of a garment score: how much of what it constrains is known."""
    total_weight = 0.0
    weighted = 0.0
    for key in scored:
        weight = DIMENSION_WEIGHTS.get(key, DEFAULT_DIMENSION_WEIGHT)
        total_weight += weight
        weighted += weight * profile[key]["confidence"]
    mean = weighted / total_weight if total_weight else 0.0
    if not constrained:
        return 0.0
    missing_ratio = 1 - len(scored) / len(constrained)
    return round(_clamp(mean * (1 - MISSING_PENALTY * missing_ratio)), 4)


def _warnings_for(dimensions: list[dict], profile: dict, ranges: dict) -> list[str]:
    warnings: list[str] = []
    for record in dimensions:
        if record["state"] == "tight":
            warnings.append(
                f"{record['label']}偏紧 {abs(record['delta_cm']):.1f}cm，建议选大一码"
            )
        elif record["state"] == "loose":
            warnings.append(
                f"{record['label']}偏松 {abs(record['delta_cm']):.1f}cm，可考虑小一码"
            )
    if not _ordered_dimensions(ranges):
        warnings.append("该款未提供任何适配区间，无法判断合身度")
    for key in _ordered_dimensions(ranges):
        entry = profile.get(key)
        if entry is None or entry["source"] != "estimated":
            continue
        if key in CONVERTED_DIMENSIONS:
            warnings.append(
                f"{_label(key)} {entry['value']:g}cm 由模型轮廓宽度换算，误差较大，建议实测"
            )
        else:
            warnings.append(
                f"{_label(key)} {entry['value']:g}cm 为模型估算值，与实测可能有偏差"
            )
    return warnings


def _suggestions_for(dimensions: list[dict], profile: dict, ranges: dict) -> list[str]:
    suggestions: list[str] = []
    tight = [record for record in dimensions if record["state"] == "tight"]
    loose = [record for record in dimensions if record["state"] == "loose"]
    if tight:
        labels = "、".join(record["label"] for record in tight)
        suggestions.append(f"{labels}偏紧，可换大一码或挑适配区间更大的款式")
    if loose:
        labels = "、".join(record["label"] for record in loose)
        suggestions.append(f"{labels}偏松，可换小一码或选带收束设计的款式")
    unknown = [
        record for record in dimensions
        if record["state"] == "unknown" and record["range"] is not None
        and record["body_value"] is None
    ]
    if unknown:
        labels = "、".join(record["label"] for record in unknown)
        suggestions.append(f"补充{labels}的实测值可以给出更准确的合身判断")
    if not _ordered_dimensions(ranges):
        suggestions.append("该款缺少适配区间数据，建议以版型与弹力描述为准")
    return suggestions


def _order_reasons(signals: list[dict]) -> list[str]:
    """Rank by impact, keep the strongest few, and never return fewer than two."""
    ordered = sorted(signals, key=lambda item: (-abs(item["influence"]), item["text"]))
    reasons = [item["text"] for item in ordered[:MAX_REASONS]]
    if len(reasons) < MIN_REASONS:
        reasons = [item["text"] for item in ordered]
        if len(reasons) < MIN_REASONS:
            reasons.append("可判断的维度较少，分数以现有数据为准")
    return reasons


def score_garment(body: dict | None, garment: dict, *, preferences: dict | None = None) -> dict:
    """Score one garment against a body profile, dimension by dimension.

    The result explains itself: ``dimensions`` carries one record per garment
    range with the body value, where that value came from, the window, the
    per-dimension score and a Chinese sentence; ``reasons`` and ``warnings``
    quote the numbers behind the total.
    """
    profile = normalize_body(body)
    item = _mapping(garment)
    ranges = _mapping(item.get("fit_ranges"))
    attributes = _mapping(item.get("attributes"))
    tags = _tags(body)
    preferences = _mapping(preferences)

    constrained = _ordered_dimensions(ranges)
    dimensions = [_score_dimension(key, profile.get(key, _missing_entry()), _range_of(ranges, key))
                  for key in constrained]
    scored = [record["key"] for record in dimensions if record["score"] is not None]

    total_weight = sum(DIMENSION_WEIGHTS.get(key, DEFAULT_DIMENSION_WEIGHT) for key in scored)
    if total_weight:
        fit_score = sum(
            DIMENSION_WEIGHTS.get(record["key"], DEFAULT_DIMENSION_WEIGHT) * record["score"]
            for record in dimensions if record["score"] is not None
        ) / total_weight
        for record in dimensions:
            if record["score"] is not None:
                weight = DIMENSION_WEIGHTS.get(record["key"], DEFAULT_DIMENSION_WEIGHT)
                record["weight"] = round(weight / total_weight, 4)
    else:
        fit_score = NEUTRAL_SCORE

    signals = _attribute_signals(tags, attributes) + _preference_signals(attributes, preferences)
    delta = sum(signal["delta"] for signal in signals)
    preference_score = _clamp(NEUTRAL_SCORE + delta)
    confidence = _confidence(profile, ranges, scored, constrained)

    raw = fit_score + PREFERENCE_WEIGHT * (preference_score - NEUTRAL_SCORE)
    factor = SHRINK_FLOOR + (1 - SHRINK_FLOOR) * confidence
    score = _clamp(NEUTRAL_SCORE + (raw - NEUTRAL_SCORE) * factor)

    ranked = [
        {"influence": abs(signal["delta"]) * PREFERENCE_WEIGHT, "text": signal["text"]}
        for signal in signals
    ]
    for record in dimensions:
        if record["score"] is None:
            continue
        ranked.append({
            "influence": abs(record["weight"] * (record["score"] - NEUTRAL_SCORE)),
            "text": record["detail"],
        })
    if not ranked:
        ranked.append({
            "influence": 0.0,
            "text": f"该款共 {len(constrained)} 项适配区间，但缺少你的人体数据，"
                    "暂时只能给出中性评分",
        })

    return {
        "score": round(score, 2),
        "fit_score": round(_clamp(fit_score), 2),
        "preference_score": round(preference_score, 2),
        "confidence": round(confidence, 2),
        "dimensions": dimensions,
        "reasons": _order_reasons(ranked),
        "warnings": _warnings_for(dimensions, profile, ranges),
        "suggestions": _suggestions_for(dimensions, profile, ranges),
    }


# --------------------------------------------------------------------------------
# look scoring
# --------------------------------------------------------------------------------


def _member_ids(look: dict, garments: list[dict]) -> list[str]:
    """Resolve the garment ids a look refers to, tolerating several shapes."""
    for key in ("garment_ids", "garments", "members", "items"):
        raw = look.get(key)
        if not isinstance(raw, (list, tuple)) or not raw:
            continue
        ids: list[str] = []
        for entry in raw:
            if isinstance(entry, str):
                ids.append(entry)
            elif isinstance(entry, dict):
                for field in ("garment_id", "id"):
                    value = entry.get(field)
                    if isinstance(value, str):
                        ids.append(value)
                        break
        if ids:
            return ids
    return [str(_mapping(item).get("id")) for item in garments if _mapping(item).get("id")]


def _derive_ranges(members: list[dict]) -> tuple[dict, list[str]]:
    """Intersect member windows per dimension; report the disagreements."""
    collected: dict[str, list[tuple[float, float]]] = {}
    for item in members:
        for key in _ordered_dimensions(_mapping(item).get("fit_ranges")):
            window = _range_of(_mapping(item).get("fit_ranges"), key)
            if window is not None:
                collected.setdefault(key, []).append(window)

    derived: dict[str, tuple[float, float]] = {}
    warnings: list[str] = []
    for key, windows in collected.items():
        low = max(window[0] for window in windows)
        high = min(window[1] for window in windows)
        if low > high:
            low = max(window[0] for window in windows)
            high = max(window[1] for window in windows)
            warnings.append(
                f"成员单品的{_label(key)}适配区间不一致，已按最紧下界 {low:g}cm "
                f"与最松上界 {high:g}cm 合并"
            )
        derived[key] = (low, high)
    return derived, warnings


def score_look(
    body: dict | None, look: dict, garments: list[dict], *, preferences: dict | None = None
) -> dict:
    """Score a whole look: member scores blended by category weight.

    The look's own ``dimensions`` come from ranges derived across its members,
    so the per-dimension explanation answers "does this *set* fit me" while the
    total answers "is this set good for me".
    """
    profile = normalize_body(body)
    look_data = _mapping(look)
    catalogue = [_mapping(item) for item in garments if _mapping(item).get("id")]
    by_id = {str(item.get("id")): item for item in catalogue}
    wanted = _member_ids(look_data, garments)
    members = [by_id[identifier] for identifier in wanted if identifier in by_id]

    scored_members = [
        (item, score_garment(body, item, preferences=preferences)) for item in members
    ]

    total_weight = 0.0
    weighted_score = 0.0
    weighted_preference = 0.0
    weighted_confidence = 0.0
    for item, result in scored_members:
        weight = CATEGORY_WEIGHTS.get(item.get("category"), DEFAULT_CATEGORY_WEIGHT)
        total_weight += weight
        weighted_score += weight * result["score"]
        weighted_preference += weight * result["preference_score"]
        weighted_confidence += weight * result["confidence"]

    if total_weight:
        member_score = weighted_score / total_weight
        preference_score = weighted_preference / total_weight
        confidence = weighted_confidence / total_weight
    else:
        member_score = NEUTRAL_SCORE
        preference_score = NEUTRAL_SCORE
        confidence = 0.0

    derived, warnings = _derive_ranges(members)
    dimensions = [
        _score_dimension(key, profile.get(key, _missing_entry()), window)
        for key, window in derived.items()
    ]
    scored = [record["key"] for record in dimensions if record["score"] is not None]
    total_dim_weight = sum(DIMENSION_WEIGHTS.get(key, DEFAULT_DIMENSION_WEIGHT) for key in scored)
    if total_dim_weight:
        fit_score = sum(
            DIMENSION_WEIGHTS.get(record["key"], DEFAULT_DIMENSION_WEIGHT) * record["score"]
            for record in dimensions if record["score"] is not None
        ) / total_dim_weight
        for record in dimensions:
            if record["score"] is not None:
                weight = DIMENSION_WEIGHTS.get(record["key"], DEFAULT_DIMENSION_WEIGHT)
                record["weight"] = round(weight / total_dim_weight, 4)
    else:
        fit_score = NEUTRAL_SCORE

    score = _clamp(member_score)

    signals: list[dict] = []
    for record in dimensions:
        if record["score"] is None:
            continue
        signals.append({
            "delta": record["score"] - NEUTRAL_SCORE,
            "influence": abs(record["weight"] * (record["score"] - NEUTRAL_SCORE)),
            "text": record["detail"],
            "kind": "dimension",
        })
    for item, result in scored_members:
        name = item.get("name") or item.get("id")
        signals.append({
            "delta": result["score"] - NEUTRAL_SCORE,
            "influence": CATEGORY_WEIGHTS.get(item.get("category"), DEFAULT_CATEGORY_WEIGHT)
            * abs(result["score"] - NEUTRAL_SCORE),
            "text": f"{name}（{item.get('category', '单品')}）单品评分 {result['score']:.2f}",
            "kind": "member",
        })
    if not signals:
        signals.append({
            "delta": 0.0, "influence": 0.0, "kind": "summary",
            "text": "该套装没有可用的成员单品数据，暂时只能给出中性评分",
        })

    warnings = list(warnings)
    for item, result in scored_members:
        for warning in result["warnings"][:1]:
            warnings.append(f"{item.get('name') or item.get('id')}：{warning}")
    suggestions: list[str] = []
    for _item, result in scored_members:
        for suggestion in result["suggestions"]:
            if suggestion not in suggestions:
                suggestions.append(suggestion)
    if not members:
        warnings.append("没有找到该套装的成员单品，无法评估整体合身度")

    return {
        "score": round(score, 2),
        "fit_score": round(_clamp(fit_score), 2),
        "preference_score": round(_clamp(preference_score), 2),
        "confidence": round(_clamp(confidence), 2),
        "dimensions": dimensions,
        "reasons": _order_reasons(signals),
        "warnings": warnings,
        "suggestions": suggestions[:MAX_REASONS],
        "members": [
            {"garment_id": item.get("id"), "category": item.get("category"),
             "score": result["score"]}
            for item, result in scored_members
        ],
    }

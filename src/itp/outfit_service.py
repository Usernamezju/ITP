"""Compose the outfit recommendation response from measured bodies and garments.

The recommendation path prefers what merchants published, then falls back to the
built-in catalogue, so the page always answers:

1. published **looks** are scored against the body's size ranges,
2. otherwise published **garments** are scored and shown one per card,
3. otherwise the built-in catalogue keeps answering with its tag based ranking.

The body comes from two places: the measurements collected on the modelling page,
and the proportions the GLB analysis measured.  ``size_match.normalize_body``
merges them and labels every value with where it came from, so the interface can
say "已填" or "估算" instead of pretending everything was measured.

This module is the only place that knows about all three of them (``wardrobe``
catalogue, ``garments`` store, ``size_match`` scoring), which keeps the HTTP
routes thin and the scoring library free of storage concerns.
"""

from __future__ import annotations

from typing import Any

from itp import wardrobe
from itp.garments import MerchantStore, public_image
from itp.size_match import normalize_body, score_garment, score_look

BODY_FIELDS = ("height_cm", "weight_kg", "shoulder_cm", "bust_cm", "waist_cm", "hip_cm")
# Enough candidates to rank meaningfully before trimming to the requested limit.
CANDIDATE_POOL = 60
# Chips show everything the published catalogue offers, so they are counted
# before the caller's filters are applied and stay switchable.
CHIP_POOL = 200
MAX_IMAGES_PER_LOOK = 8


def body_inputs(profile: dict | None, analysis: dict[str, Any]) -> dict[str, Any]:
    """Merge the stored measurements with the model proportions for scoring.

    ``profile`` is the row from the body-profile store (values may all be None);
    ``analysis`` is the document built by :mod:`itp.wardrobe`, whose ``ratios``
    and ``tag_families`` fields carry the machine readable model numbers.
    """
    body: dict[str, Any] = {field: (profile or {}).get(field) for field in BODY_FIELDS}
    ratios = analysis.get("ratios") or {}
    families = analysis.get("tag_families") or {}
    if analysis.get("available") and ratios:
        body["model"] = {
            "available": True,
            **ratios,
            "tags": families,
            "labels": analysis.get("labels") or {},
        }
    else:
        body["model"] = {"available": False}
    return body


def _has_measurements(profile: dict | None) -> bool:
    return any((profile or {}).get(field) is not None for field in BODY_FIELDS)


def _matches(
    item: dict[str, Any], style: str | None, season: str | None, occasion: str | None
) -> bool:
    """A look row or a garment metrics dict; only the requested facets must match."""
    for key, wanted in (("style", style), ("season", season), ("occasion", occasion)):
        if wanted and item.get(key) != wanted:
            return False
    return True


def _member_metrics(member: dict[str, Any]) -> dict[str, Any]:
    """A look member may arrive as a garment row (metrics nested) or as metrics."""
    nested = member.get("metrics")
    return nested if isinstance(nested, dict) else member


def _scoring_member(member: dict[str, Any]) -> dict[str, Any]:
    """One flat dict per garment for the scoring library: its id plus its metrics.

    The library matches a look's ``items`` against these ids and reads the size
    ranges from the top level, so neither a bare metrics dict nor a row with a
    nested ``metrics`` key works on its own.
    """
    return {"id": member["id"], **_member_metrics(member)}


def _attribute_note(metrics: dict[str, Any]) -> str:
    attributes = metrics.get("attributes") or {}
    parts: list[str] = []
    if attributes.get("silhouette"):
        parts.append(str(attributes["silhouette"]))
    if attributes.get("stretch"):
        parts.append(str(attributes["stretch"]))
    weight = attributes.get("weight_gsm")
    if isinstance(weight, (int, float)) and weight > 0:
        parts.append(f"{int(weight)}g/m²")
    return " · ".join(parts) or "商家未填写说明"


def _item(metrics: dict[str, Any]) -> dict[str, Any]:
    attributes = metrics.get("attributes") or {}
    return {
        "category": metrics.get("category") or "",
        "name": metrics.get("name") or "未命名单品",
        "color": attributes.get("color") or "#cccccc",
        "note": metrics.get("description") or _attribute_note(metrics),
    }


def _palette(members: list[dict[str, Any]], stored: Any) -> list[str]:
    colors: list[str] = []
    if isinstance(stored, list):
        colors = [color for color in stored if isinstance(color, str) and color]
    for member in members:
        color = (_member_metrics(member).get("attributes") or {}).get("color")
        if isinstance(color, str) and color and color not in colors:
            colors.append(color)
    return colors[:4] or ["#cccccc"]


def _image_urls(images: list[dict[str, Any]]) -> list[str]:
    """Stored image rows are turned into the public URL the browser can fetch."""
    return [public_image(image)["url"] for image in images][:MAX_IMAGES_PER_LOOK]


def _tagline(story: Any, members: list[dict[str, Any]]) -> str:
    if isinstance(story, str) and story.strip():
        return story.strip().splitlines()[0][:28]
    categories = [str(_member_metrics(member).get("category") or "") for member in members]
    return " · ".join([category for category in categories if category][:3]) or "商家套装"


def _look_payload(
    look: dict[str, Any],
    members: list[dict[str, Any]],
    images: dict[str, list[dict[str, Any]]],
    fit: dict[str, Any],
) -> dict[str, Any]:
    gallery = [url for member in members for url in _image_urls(images.get(member["id"], []))]
    reasons = fit.get("reasons") or []
    return {
        "id": look["id"],
        "name": look.get("name") or "商家套装",
        "tagline": _tagline(look.get("story"), members),
        "story": look.get("story") or "",
        "style": look.get("style") or "",
        "season": look.get("season") or "",
        "occasion": look.get("occasion") or "",
        "palette": _palette(members, look.get("palette")),
        "items": [_item(_member_metrics(member)) for member in members],
        "tips": [],
        "avoid": "",
        "reason": reasons[0] if reasons else "按尺码指标匹配",
        "score": fit.get("score", 0.0),
        "matched": [],
        "fit": fit,
        "origin": "database",
        "image_url": gallery[0] if gallery else None,
        "image_urls": gallery,
    }


def _garment_payload(
    garment: dict[str, Any],
    images: dict[str, list[dict[str, Any]]],
    fit: dict[str, Any],
) -> dict[str, Any]:
    metrics = _member_metrics(garment)
    gallery = _image_urls(images.get(garment["id"], []))
    reasons = fit.get("reasons") or []
    return {
        "id": garment["id"],
        "name": metrics.get("name") or "商家单品",
        "tagline": metrics.get("brand") or _attribute_note(metrics),
        "story": metrics.get("description") or "",
        "style": metrics.get("style") or "",
        "season": metrics.get("season") or "",
        "occasion": metrics.get("occasion") or "",
        "palette": _palette([metrics], None),
        "items": [_item(metrics)],
        "tips": list(metrics.get("tips") or []),
        "avoid": "",
        "reason": reasons[0] if reasons else "按尺码指标匹配",
        "score": fit.get("score", 0.0),
        "matched": [],
        "fit": fit,
        "origin": "database",
        "image_url": gallery[0] if gallery else None,
        "image_urls": gallery,
    }


def _filters(values: list[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return [
        {"id": key, "count": counts[key]}
        for key in sorted(counts, key=lambda key: (-counts[key], key))
    ]


def _published_members(
    merchants: MerchantStore, look: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Members of a look that are still published, together with their images."""
    members: list[dict[str, Any]] = []
    for garment_id in look.get("items") or []:
        garment = merchants.garment(garment_id)
        if garment and garment.get("status") == "published":
            members.append(garment)
    images = merchants.images_for_many([member["id"] for member in members]) if members else {}
    return members, images


def recommend(
    store: Any,
    merchants: MerchantStore | None,
    *,
    job_id: str | None = None,
    asset_id: str | None = None,
    style: str | None = None,
    season: str | None = None,
    occasion: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Assemble the whole ``GET /api/outfits`` body, measurements first.

    ``source`` keeps its original meaning (whether a model was analysed); where
    each recommendation came from is carried per item as ``origin``.
    """
    report = wardrobe.outfit_report(
        store, job_id=job_id, asset_id=asset_id, style=style, season=season,
        occasion=occasion, limit=limit,
    )
    profile = merchants.body_profile(job_id) if merchants else None
    inputs = body_inputs(profile, report["analysis"])
    report["analysis"]["body"] = normalize_body(inputs)
    if not merchants:
        return report

    _look_total, all_looks = merchants.list_published_looks(limit=CHIP_POOL)
    looks = [look for look in all_looks if _matches(look, style, season, occasion)]
    if looks:
        scored = []
        for look in looks:
            members, images = _published_members(merchants, look)
            if not members:
                continue
            fit = score_look(inputs, look, [_scoring_member(member) for member in members])
            scored.append((fit["score"], _look_payload(look, members, images, fit)))
        if scored:
            scored.sort(key=lambda item: (-item[0], item[1]["id"]))
            report["recommendations"] = [payload for _score, payload in scored[:limit]]
            report["filters"] = {
                "styles": _filters([str(look.get("style") or "") for look in all_looks]),
                "seasons": _filters([str(look.get("season") or "") for look in all_looks]),
                "occasions": _filters([str(look.get("occasion") or "") for look in all_looks]),
            }
            return report

    _garment_total, all_garments = merchants.list_published_garments(limit=CHIP_POOL)
    garments = [
        garment for garment in all_garments
        if _matches(garment["metrics"], style, season, occasion)
    ][:CANDIDATE_POOL]
    if garments:
        scored = [
            (score_garment(inputs, _scoring_member(garment)), garment) for garment in garments
        ]
        scored.sort(key=lambda item: (-item[0]["score"], item[1]["id"]))
        top = scored[:limit]
        images = merchants.images_for_many([garment["id"] for _fit, garment in top])
        report["recommendations"] = [
            _garment_payload(garment, images, fit) for fit, garment in top
        ]
        report["filters"] = {
            "styles": _filters([str(g["metrics"].get("style") or "") for g in all_garments]),
            "seasons": _filters([str(g["metrics"].get("season") or "") for g in all_garments]),
            "occasions": _filters([str(g["metrics"].get("occasion") or "") for g in all_garments]),
        }
        return report

    # Nothing published yet: the built-in catalogue keeps answering, labelled so
    # the interface can say where the recommendation came from.
    for outfit in report["recommendations"]:
        outfit["origin"] = "catalogue"
    if not _has_measurements(profile) and not report["analysis"]["available"]:
        report["analysis"]["notes"] = list(report["analysis"]["notes"]) + [
            "商家尚未发布商品，当前使用内置穿搭目录；填写身高或导入商品后可按尺码指标匹配。"
        ]
    return report

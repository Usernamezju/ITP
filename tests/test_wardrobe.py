import json
import re
import struct

import numpy as np
import pytest
from auth_helpers import fund_client
from fastapi.testclient import TestClient

from itp import wardrobe
from itp.api import create_app
from itp.wardrobe import (
    CATALOG,
    PROFILE_BANDS,
    TAG_FAMILIES,
    analyze_glb,
    catalog_filters,
    recommend_outfits,
)

HEX = re.compile(r"^#[0-9a-f]{6}$")
ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STYLES = {"通勤", "休闲", "街头", "运动", "度假", "复古", "极简", "学院"}
SEASONS = {"春", "夏", "秋", "冬", "四季"}
HEIGHT = 1.8
HALF_DEPTH = 0.10
# Widest horizontal span per band, in metres, for a synthetic slim figure whose
# shoulders sit in band 3, whose waist is narrowest around bands 8-9 and whose
# hip peaks in band 10.
SPANS = [
    0.12, 0.16, 0.22, 0.36, 0.36, 0.35, 0.33, 0.31, 0.30, 0.30,
    0.34, 0.33, 0.31, 0.28, 0.26, 0.24, 0.18, 0.16, 0.12, 0.14,
]


def body_positions(*, arms_out: bool = False) -> np.ndarray:
    """Rings of four corners per band plus two points fixing the stature."""
    points = [[0.0, HEIGHT, 0.0]]
    for band, span in enumerate(SPANS):
        half = span / 2
        if arms_out and band == 4:
            half = HEIGHT / 2
        for fraction in (0.35, 0.65):
            y = HEIGHT * (1 - (band + fraction) / PROFILE_BANDS)
            for sign_x in (-1, 1):
                for sign_z in (-1, 1):
                    points.append([sign_x * half, y, sign_z * HALF_DEPTH])
    points.append([0.0, 0.0, 0.0])
    return np.array(points, dtype=np.float64)


def glb_bytes(positions: np.ndarray | None = None, mutate=None) -> bytes:
    """Build a minimal but valid GLB container around one POSITION accessor."""
    array = np.asarray(body_positions() if positions is None else positions, dtype="<f4")
    binary = array.tobytes()
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(array), "type": "VEC3"}
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    if mutate is not None:
        mutate(document)
    chunk = json.dumps(document).encode("utf-8")
    chunk += b" " * ((-len(chunk)) % 4)
    payload = binary + b"\x00" * ((-len(binary)) % 4)
    total = 12 + 8 + len(chunk) + 8 + len(payload)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(chunk), 0x4E4F534A)
        + chunk
        + struct.pack("<II", len(payload), 0x004E4942)
        + payload
    )


def write_glb(tmp_path, name="body.glb", **kwargs):
    path = tmp_path / name
    path.write_bytes(glb_bytes(**kwargs))
    return path


def client_for(settings):
    app = create_app(settings, start_worker=False)
    return fund_client(TestClient(app, base_url="http://localhost:8000"))


def add_mesh(store, payload: bytes, *, owner=None) -> str:
    asset_id, path = store.new_asset_path("glb")
    path.write_bytes(payload)
    store.add_asset(asset_id, path, "model", format="GLB", owner_id=owner)
    return asset_id


# --------------------------------------------------------------------------- catalogue


def test_catalogue_entries_are_complete_and_consistent():
    assert 16 <= len(CATALOG) <= 20
    assert len({outfit["id"] for outfit in CATALOG}) == len(CATALOG)
    assert len({frozenset(outfit["palette"]) for outfit in CATALOG}) == len(CATALOG)
    for outfit in CATALOG:
        assert ID.fullmatch(outfit["id"]), outfit["id"]
        assert 4 <= len(outfit["name"]) <= 6, outfit["id"]
        assert 60 <= len(outfit["story"]) <= 110, outfit["id"]
        assert outfit["tagline"]
        assert outfit["style"] in STYLES
        assert outfit["season"] in SEASONS
        assert outfit["occasion"]
        assert 3 <= len(outfit["palette"]) <= 4
        assert all(HEX.fullmatch(color) for color in outfit["palette"])
        assert len(set(outfit["palette"])) == len(outfit["palette"])
        assert 4 <= len(outfit["items"]) <= 6
        for item in outfit["items"]:
            assert set(item) == {"category", "name", "color", "note"}
            assert item["category"] and item["name"] and item["note"]
            assert HEX.fullmatch(item["color"])
        assert 2 <= len(outfit["tips"]) <= 3
        assert outfit["avoid"]


def test_catalogue_tags_cover_the_vocabulary_and_the_reasons():
    for outfit in CATALOG:
        tags = outfit["tags"]
        assert len(tags) == 3
        assert set(tags) == set(outfit["reasons"])
        for family, values in TAG_FAMILIES.items():
            assert len(set(tags) & set(values)) == 1, (outfit["id"], family)
    covered = {tag for outfit in CATALOG for tag in outfit["tags"]}
    assert covered == {tag for values in TAG_FAMILIES.values() for tag in values}


def test_catalogue_filters_count_every_outfit():
    filters = catalog_filters()
    assert set(filters) == {"styles", "seasons", "occasions"}
    for key, field in (("styles", "style"), ("seasons", "season"), ("occasions", "occasion")):
        entries = filters[key]
        assert len({entry["id"] for entry in entries}) == len(entries)
        assert sum(entry["count"] for entry in entries) == len(CATALOG)
        assert entries == sorted(entries, key=lambda entry: (-entry["count"], entry["id"]))
        counted = {outfit[field] for outfit in CATALOG}
        assert {entry["id"] for entry in entries} == counted


# --------------------------------------------------------------------------- analysis


def test_analyze_glb_describes_a_synthetic_body(tmp_path):
    geometry = analyze_glb(write_glb(tmp_path))
    assert geometry is not None
    assert geometry["height"] == pytest.approx(HEIGHT)
    assert geometry["width"] == pytest.approx(0.36)
    assert geometry["depth"] == pytest.approx(HALF_DEPTH * 2)
    profile = geometry["profile"]
    assert len(profile) == PROFILE_BANDS
    assert all(0.0 <= value <= 1.0 for value in profile)
    assert profile[0] < profile[3]
    assert geometry["tags"] == {"build": "slim", "volume": "light", "legs": "long-leg"}
    assert geometry["labels"] == {"build": "修长", "volume": "轻量", "legs": "长腿型"}
    assert 4 <= len(geometry["metrics"]) <= 6
    for metric in geometry["metrics"]:
        assert set(metric) == {"label", "value", "hint"}
        assert re.fullmatch(r"-?\d+\.\d{2}|—", metric["value"])
        assert metric["hint"]
    assert geometry["metrics"][0]["label"] == "身高 / 肩宽"
    assert geometry["metrics"][0]["value"] == "5.00"
    assert any("20 段" in note for note in geometry["notes"])


def test_analyze_glb_survives_outstretched_arms(tmp_path):
    plain = analyze_glb(write_glb(tmp_path, name="plain.glb"))
    armed = analyze_glb(write_glb(tmp_path, name="arms.glb", positions=body_positions(
        arms_out=True)))
    assert plain is not None and armed is not None
    assert armed["width"] > plain["width"]
    assert max(armed["profile"]) > 0.9
    # The arm band must not turn a slim figure into a broad one.
    assert armed["tags"]["build"] == "slim"
    assert any("剔除" in note for note in armed["notes"])


def test_analyze_glb_is_deterministic(tmp_path):
    path = write_glb(tmp_path)
    assert analyze_glb(path) == analyze_glb(path)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda doc: doc["meshes"][0]["primitives"][0].update(
                {
                    "extensions": {
                        "KHR_draco_mesh_compression": {
                            "bufferView": 0,
                            "attributes": {"POSITION": 0},
                        }
                    }
                }
            ),
            id="draco",
        ),
        pytest.param(lambda doc: doc["accessors"][0].update({"count": 6_000_000}), id="vertices"),
        pytest.param(lambda doc: doc["accessors"][0].update({"byteOffset": 1 << 20}), id="bounds"),
        pytest.param(lambda doc: doc["accessors"][0].update({"componentType": 5123}), id="type"),
        pytest.param(lambda doc: doc["accessors"][0].update({"type": "VEC2"}), id="vec2"),
        pytest.param(lambda doc: doc["accessors"][0].update({"sparse": {"count": 1}}), id="sparse"),
        pytest.param(lambda doc: doc["buffers"][0].update({"uri": "mesh.bin"}), id="external"),
        pytest.param(lambda doc: doc.update({"meshes": []}), id="no-mesh"),
        pytest.param(lambda doc: doc.update({"bufferViews": []}), id="no-views"),
    ],
)
def test_analyze_glb_rejects_unsupported_geometry(tmp_path, mutate):
    path = tmp_path / "broken.glb"
    path.write_bytes(glb_bytes(mutate=mutate))
    assert analyze_glb(path) is None


def test_analyze_glb_rejects_broken_or_oversized_files(tmp_path):
    valid = glb_bytes()
    assert analyze_glb(None) is None
    assert analyze_glb(tmp_path / "missing.glb") is None
    empty = tmp_path / "empty.glb"
    empty.write_bytes(b"")
    assert analyze_glb(empty) is None
    garbage = tmp_path / "garbage.glb"
    garbage.write_bytes(b"definitely not a glb container")
    assert analyze_glb(garbage) is None
    truncated = tmp_path / "truncated.glb"
    truncated.write_bytes(valid[: len(valid) // 2])
    assert analyze_glb(truncated) is None
    healthy = tmp_path / "healthy.glb"
    healthy.write_bytes(valid)
    assert analyze_glb(healthy) is not None


def test_analyze_glb_rejects_a_file_above_the_size_cap(tmp_path, monkeypatch):
    path = write_glb(tmp_path)
    assert analyze_glb(path) is not None
    monkeypatch.setattr(wardrobe, "MAX_GLB_BYTES", 64)
    assert analyze_glb(path) is None


# --------------------------------------------------------------------------- recommendations


def test_recommendations_lead_with_the_matching_tags(tmp_path):
    geometry = analyze_glb(write_glb(tmp_path))
    outfits = recommend_outfits(geometry)
    assert outfits[0]["id"] == "soft-tailoring"
    assert outfits[0]["matched"] == ["slim", "light", "long-leg"]
    assert outfits[0]["score"] == 0.78
    scores = [outfit["score"] for outfit in outfits]
    assert scores == sorted(scores, reverse=True)
    for outfit in outfits:
        assert 0.0 <= outfit["score"] <= 1.0
        assert round(outfit["score"], 2) == outfit["score"]
        assert set(outfit) == {
            "id", "name", "tagline", "story", "style", "season", "occasion",
            "palette", "items", "tips", "avoid", "reason", "score", "matched",
        }
        assert outfit["reason"]
        assert "reasons" not in outfit and "tags" not in outfit


def test_recommendations_honour_limit_and_stay_stable(tmp_path):
    geometry = analyze_glb(write_glb(tmp_path))
    assert len(recommend_outfits(geometry, limit=3)) == 3
    assert len(recommend_outfits(geometry, limit=24)) == len(CATALOG)
    first = recommend_outfits(geometry, style="通勤", season="秋", limit=5)
    second = recommend_outfits(geometry, style="通勤", season="春", limit=5)
    assert recommend_outfits(geometry, limit=5) == recommend_outfits(geometry, limit=5)
    assert first != second
    assert second != recommend_outfits(geometry, limit=5)


def test_filters_rerank_without_emptying_the_catalogue():
    outfits = recommend_outfits(None, style="通勤", limit=6)
    commute = [entry["id"] for entry in CATALOG if entry["style"] == "通勤"]
    assert [outfit["id"] for outfit in outfits[:len(commute)]] == commute
    assert all("通勤" in outfit["matched"] for outfit in outfits[:len(commute)])
    assert all(outfit["style"] != "通勤" for outfit in outfits[len(commute):])
    unmatched = recommend_outfits(None, style="不存在的风格", limit=24)
    assert len(unmatched) == len(CATALOG)
    assert all(outfit["score"] >= 0.10 for outfit in unmatched)
    assert all("不存在的风格" not in outfit["matched"] for outfit in unmatched)


def test_recommendations_without_a_model_are_generic_in_catalogue_order():
    outfits = recommend_outfits(None)
    assert [outfit["score"] for outfit in outfits] == [0.30] * len(outfits)
    assert [outfit["id"] for outfit in outfits] == [entry["id"] for entry in CATALOG][:len(outfits)]
    assert all(outfit["matched"] == [] for outfit in outfits)
    assert all("通用体型" in outfit["reason"] for outfit in outfits)


# --------------------------------------------------------------------------- HTTP


def test_outfits_endpoint_without_a_model(settings):
    with client_for(settings) as client:
        response = client.get("/api/outfits")
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "default"
        analysis = body["analysis"]
        assert analysis["available"] is False
        assert analysis["method"] == "从三维模型包围盒与轮廓切片估算"
        assert analysis["profile"] is None
        assert analysis["labels"] == {} and analysis["metrics"] == [] and analysis["tags"] == []
        assert any("尚未生成三维模型" in note for note in analysis["notes"])
        assert len(body["recommendations"]) == 6
        assert body["filters"] == catalog_filters()


def test_outfits_endpoint_limit_is_validated(settings):
    with client_for(settings) as client:
        assert len(client.get("/api/outfits?limit=1").json()["recommendations"]) == 1
        assert len(client.get("/api/outfits?limit=24").json()["recommendations"]) == len(CATALOG)
        for query in ("limit=0", "limit=25", "limit=-1", "limit=abc"):
            assert client.get(f"/api/outfits?{query}").status_code == 422


def test_outfits_endpoint_ignores_job_references_it_no_longer_reads(settings):
    with client_for(settings) as client:
        # Old pages still send job_id/asset_id; the public catalogue has no
        # customer data in it, so the extra parameters change nothing.
        for query in ("job_id=missing", "asset_id=missing", "job_id=missing&asset_id=missing"):
            response = client.get(f"/api/outfits?{query}")
            assert response.status_code == 200
            assert response.json()["source"] == "default"


def test_recommend_endpoint_analyses_a_temporary_mesh(settings):
    with client_for(settings) as client:
        store = client.app.state.store
        owner = client.app.state.merchants.merchant_by_name("model-tester")["id"]
        mesh = add_mesh(store, glb_bytes(), owner=owner)
        response = client.post("/api/outfits/recommend", json={
            "asset_id": mesh, "pose_mode": "t-pose",
            "measurements": {"height_cm": 180.0, "weight_kg": 68.0},
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "model"
        analysis = body["analysis"]
        assert analysis["available"] is True
        assert analysis["labels"]["pose"] == "T-Pose"
        assert analysis["labels"]["build"] == "修长"
        assert analysis["tags"] == ["slim", "light", "long-leg"]
        assert len(analysis["profile"]) == PROFILE_BANDS
        assert 4 <= len(analysis["metrics"]) <= 6
        # Typed numbers score along the model, and are labelled as typed.
        assert analysis["body"]["height_cm"]["source"] == "input"
        assert analysis["body"]["shoulder_cm"]["source"] == "estimated"
        assert body["recommendations"][0]["id"] == "soft-tailoring"
        # The upload was temporary: it is gone as soon as the answer is out.
        assert client.get(f"/api/assets/{mesh}").status_code == 404


def test_recommend_endpoint_marks_an_unrecorded_pose(settings):
    with client_for(settings) as client:
        store = client.app.state.store
        owner = client.app.state.merchants.merchant_by_name("model-tester")["id"]
        mesh = add_mesh(store, glb_bytes(), owner=owner)
        body = client.post("/api/outfits/recommend", json={"asset_id": mesh}).json()
        assert body["analysis"]["labels"]["pose"] == "未记录"
        assert any("任务未记录姿态" in note for note in body["analysis"]["notes"])


def test_recommend_endpoint_survives_a_broken_mesh(settings):
    with client_for(settings) as client:
        store = client.app.state.store
        owner = client.app.state.merchants.merchant_by_name("model-tester")["id"]
        mesh = add_mesh(store, b"glTF but not really", owner=owner)
        body = client.post("/api/outfits/recommend", json={"asset_id": mesh}).json()
        assert body["source"] == "default"
        assert body["analysis"]["available"] is False
        assert len(body["recommendations"]) == 6


def test_recommend_endpoint_refuses_an_expired_upload(settings):
    with client_for(settings) as client:
        assert client.post("/api/outfits/recommend",
                           json={"asset_id": "0" * 32}).status_code == 404
        assert client.post("/api/outfits/recommend",
                           json={"asset_id": "missing"}).status_code == 422
        assert client.post("/api/outfits/recommend",
                           json={"pose_mode": "sitting"}).status_code == 422


def test_outfits_endpoint_is_deterministic_and_ignores_filters_in_counts(settings):
    with client_for(settings) as client:
        plain = client.get("/api/outfits")
        filtered = client.get("/api/outfits?style=通勤&season=秋&occasion=通勤办公")
        assert plain.status_code == filtered.status_code == 200
        assert plain.json()["filters"] == filtered.json()["filters"]
        assert client.get("/api/outfits").text == plain.text
        assert filtered.json()["recommendations"][0]["matched"]

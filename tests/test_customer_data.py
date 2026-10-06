"""Customer files are borrowed, never kept: the browser owns the originals.

Every route here is part of the same contract — a photo, a model or a set of
measurements enters a temporary workspace for exactly one calculation (or one
job), and the browser's acknowledgment removes the server's last copy.
"""

import pytest
from auth_helpers import fund_client
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_commerce import triangle_glb
from test_face_refine import face_settings, highres_photo
from test_face_refine import glb_bytes as face_glb
from test_wardrobe import glb_bytes

from itp.api import create_app
from itp.tryon import VIEWS

GLB = glb_bytes()
MEASUREMENTS = {"height_cm": 180, "weight_kg": 62, "shoulder_cm": 42}


def client_for(settings):
    app = create_app(settings, start_worker=False)
    return fund_client(TestClient(app, base_url="http://localhost:8000"))


def switch_to_second_account(client, name="other-customer"):
    """Register a second, unrelated account and use it; return the first header."""
    previous = client.headers.pop("Authorization")
    registered = client.post("/api/auth/register", json={
        "name": name, "display_name": "Other", "password": "another-password",
    })
    assert registered.status_code == 201, registered.text
    token = client.post("/api/auth/login", json={
        "name": name, "password": "another-password",
    }).json()["access_token"]
    client.headers["Authorization"] = "Bearer " + token
    return previous


# --------------------------------------------------------------------------- models


def test_model_upload_requires_an_account_and_a_real_glb(settings):
    with client_for(settings) as client:
        token = client.headers.pop("Authorization")
        upload = client.post("/api/model-assets", files={"file": ("body.glb", GLB)})
        assert upload.status_code == 401
        client.headers["Authorization"] = token
        assert client.post(
            "/api/model-assets", files={"file": ("body.glb", b"not a container")}
        ).status_code == 422
        uploaded = client.post("/api/model-assets", files={"file": ("body.glb", GLB)})
        assert uploaded.status_code == 201, uploaded.text
        asset = uploaded.json()
        assert asset["kind"] == "model" and asset["format"] == "GLB"
        assert "filename" not in asset
        assert client.get(asset["url"]).content == GLB
        described = client.get(f"/api/assets/{asset['id']}")
        assert described.status_code == 200
        assert described.json()["kind"] == "model"
        assert "filename" not in described.text


def test_model_upload_is_bounded_and_private_to_its_owner(settings, monkeypatch):
    def upload(client):
        return client.post("/api/model-assets", files={"file": ("body.glb", GLB)})

    with client_for(settings) as client:
        asset = upload(client).json()
        # The middleware refuses an oversized body before the route sees it...
        monkeypatch.setattr("itp.api.MODEL_BODY_LIMIT", 64)
        assert upload(client).status_code == 413
        # ...and the route refuses a body the middleware let through.
        monkeypatch.setattr("itp.api.MODEL_BODY_LIMIT", 64 + 65536)
        monkeypatch.setattr("itp.api.MAX_MODEL_UPLOAD", 64)
        assert upload(client).status_code == 422
        switch_to_second_account(client)
        assert client.get(asset["url"]).status_code == 404
        assert client.post(
            "/api/outfits/recommend", json={"asset_id": asset["id"], "measurements": MEASUREMENTS}
        ).status_code == 404


# --------------------------------------------------------------------------- recommendations


def test_recommendation_measures_the_upload_then_deletes_it(settings):
    with client_for(settings) as client:
        uploaded = client.post("/api/model-assets", files={"file": ("body.glb", GLB)}).json()
        answered = client.post("/api/outfits/recommend", json={
            "asset_id": uploaded["id"], "measurements": MEASUREMENTS, "limit": 4,
        })
        assert answered.status_code == 200, answered.text
        body = answered.json()
        # The temporary model really was measured, and the typed numbers won.
        assert body["source"] == "model"
        assert body["analysis"]["available"] is True
        assert body["analysis"]["labels"]["build"] == "修长"
        assert body["analysis"]["body"]["height_cm"]["value"] == 180.0
        assert body["analysis"]["body"]["height_cm"]["source"] == "input"
        assert body["analysis"]["body"]["hip_cm"]["source"] == "estimated"
        assert len(body["recommendations"]) == 4
        assert str(settings.data_dir) not in answered.text
        # Neither the bytes nor the row survive the answer.
        store = client.app.state.store
        assert store.asset(uploaded["id"]) is None
        assert client.get(uploaded["url"]).status_code == 404


def test_recommendation_without_a_model_still_uses_typed_measurements(settings):
    with client_for(settings) as client:
        answered = client.post(
            "/api/outfits/recommend", json={"measurements": {"height_cm": 165}}
        )
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert body["source"] == "default"
        assert body["analysis"]["body"]["height_cm"]["value"] == 165.0
        assert body["recommendations"]


def test_recommendation_refuses_unknown_data_and_keeps_the_upload(settings, image_bytes):
    with client_for(settings) as client:
        uploaded = client.post("/api/model-assets", files={"file": ("body.glb", GLB)}).json()
        picture = client.post("/api/assets", files={"file": ("a.png", image_bytes)}).json()
        for payload in (
            {"asset_id": uploaded["id"], "measurements": {"height_cm": 400}},
            {"asset_id": uploaded["id"], "measurements": {"shoe_size": 42}},
            {"asset_id": uploaded["id"], "measurements": {"height_cm": "tall"}},
            {"asset_id": picture["id"], "measurements": MEASUREMENTS},
            {"asset_id": uploaded["id"], "unknown": True},
            {"asset_id": uploaded["id"], "limit": 25},
        ):
            assert client.post("/api/outfits/recommend", json=payload).status_code == 422, payload
        # A refused request spends nothing, so the upload is still usable.
        assert client.get(uploaded["url"]).status_code == 200
        assert client.post("/api/outfits/recommend", json={
            "asset_id": uploaded["id"], "measurements": MEASUREMENTS,
        }).status_code == 200
        assert client.get(uploaded["url"]).status_code == 404


# --------------------------------------------------------------------------- jobs


def test_job_results_leave_the_server_when_the_browser_acknowledges(settings, image_bytes):
    from test_pipeline import Cloud

    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        photo = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        created = client.post("/api/jobs", json={"front": photo["id"], "texture": False}).json()
        assert client.post(f"/api/jobs/{created['id']}/acknowledge").status_code == 409
        app.state.pipeline.cloud = Cloud()
        app.state.pipeline.fetch = lambda url, path, **kwargs: path.write_bytes(triangle_glb())
        app.state.pipeline.run_job(app.state.store.job(created["id"]))
        job = client.get(f"/api/jobs/{created['id']}").json()
        assert job["state"] == "succeeded"
        mesh = job["artifacts"][0]["asset_id"]
        assert client.get(f"/api/assets/{mesh}/file").status_code == 200
        # The input photo already left when the run finished.
        assert client.get(f"/api/assets/{photo['id']}/file").status_code == 404
        previous = switch_to_second_account(client)
        assert client.post(f"/api/jobs/{created['id']}/acknowledge").status_code == 404
        assert client.get(f"/api/assets/{mesh}/file").status_code == 404
        client.headers["Authorization"] = previous
        assert client.post(f"/api/jobs/{created['id']}/acknowledge").status_code == 204
        assert client.get(f"/api/assets/{mesh}/file").status_code == 404
        assert client.get(f"/api/jobs/{created['id']}").status_code == 404
        assert client.get("/api/jobs").json() == []


def test_tryon_results_leave_the_server_when_the_browser_acknowledges(settings, image_bytes):
    settings = settings.model_copy(update={
        "seedream_endpoint": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        "seedream_api_key": SecretStr("test-only"),
    })
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        photo = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        created = client.post("/api/tryons", json={
            "person": {view: photo["id"] for view in VIEWS},
            "garment": {view: photo["id"] for view in VIEWS},
        })
        assert created.status_code == 201, created.text
        tryon = created.json()
        assert client.post(f"/api/tryons/{tryon['id']}/acknowledge").status_code == 409
        app.state.tryon_worker.providers["seedream"].generate = (
            lambda paths, prompt, model: image_bytes
        )
        app.state.tryon_worker.run_job(tryon)
        ready = client.get(f"/api/tryons/{tryon['id']}").json()
        assert ready["state"] == "ready"
        front = ready["results"]["front"]
        assert client.get(f"/api/assets/{front}/file").status_code == 200
        previous = switch_to_second_account(client)
        assert client.post(f"/api/tryons/{tryon['id']}/acknowledge").status_code == 404
        assert client.get(f"/api/assets/{front}/file").status_code == 404
        client.headers["Authorization"] = previous
        assert client.post(f"/api/tryons/{tryon['id']}/acknowledge").status_code == 204
        assert client.get(f"/api/tryons/{tryon['id']}").status_code == 404
        assert client.get(f"/api/assets/{front}/file").status_code == 404
        assert client.get("/api/tryons").json() == []


def test_face_refinement_asset_leaves_the_server_after_acknowledgment(settings):
    from itp.face_refine import REQUIRED_OPERATIONS

    app = create_app(face_settings(settings), start_worker=False)
    report = {"face_bbox": [200, 100, 800, 850], "operations": sorted(REQUIRED_OPERATIONS)}
    app.state.face_worker.provider.refine = lambda mesh, photo, model: (face_glb(), report)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        store = app.state.store
        owner = app.state.merchants.merchant_by_name("model-tester")["id"]
        mesh_id, mesh_path = store.new_asset_path("glb")
        mesh_path.write_bytes(face_glb())
        store.add_asset(mesh_id, mesh_path, "model", format="GLB", owner_id=owner)
        source = store.create_job({"name": "模型", "front": "f" * 32}, owner_id=owner)
        source["state"] = "succeeded"
        source["artifacts"].append(
            {"asset_id": mesh_id, "stage": "geometry", "format": "GLB", "index": 0}
        )
        store.save_job(source)
        photo = client.post(
            "/api/face-photos", files={"file": ("photo.jpg", highres_photo())}
        ).json()
        created = client.post(
            f"/api/jobs/{source['id']}/face-refinement", json={"face_photo": photo["id"]}
        )
        assert created.status_code == 201, created.text
        item = created.json()
        app.state.face_worker.run_job(item)
        ready = client.get(f"/api/jobs/{source['id']}/face-refinement").json()[0]
        assert ready["state"] == "ready"
        result = ready["result_asset"]
        assert client.get(f"/api/assets/{result}/file").status_code == 200
        assert client.post(f"/api/face-refinements/{item['id']}/acknowledge").status_code == 204
        assert client.get(f"/api/jobs/{source['id']}/face-refinement").json() == []
        assert client.get(f"/api/assets/{result}/file").status_code == 404


@pytest.mark.parametrize("path", [
    "/api/jobs/{}/acknowledge",
    "/api/tryons/{}/acknowledge",
    "/api/face-refinements/{}/acknowledge",
])
def test_acknowledgment_refuses_unknown_tasks(settings, path):
    with client_for(settings) as client:
        assert client.post(path.format("missing")).status_code == 404
        switch_to_second_account(client)
        assert client.post(path.format("missing")).status_code == 404

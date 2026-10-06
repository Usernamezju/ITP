from fastapi.testclient import TestClient

from itp.api import create_app
from auth_helpers import fund_client
from itp.schemas import JobRequest


def test_six_view_schema_and_confirmation():
    all_views = {key: f"{index:032x}" for index, key in enumerate(
        ("back", "left", "right", "left_front", "right_front"), 1
    )}
    request = JobRequest(front="f" * 32, views=all_views, views_consistent_confirmed=True)
    assert len(request.views) == 5
    try:
        JobRequest(front="f" * 32, views=all_views)
    except ValueError as exc:
        assert "同一人物" in str(exc)
    else:
        raise AssertionError("Unconfirmed multi-view inputs must be rejected")


def test_six_views_reach_geometry_provider(settings, image_bytes, monkeypatch):
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        ids = {}
        for view in ("front", "back", "left", "right", "left_front", "right_front"):
            response = client.post("/api/assets", files={"file": (f"{view}.png", image_bytes)})
            assert response.status_code == 201
            ids[view] = response.json()["id"]
        body = {"front": ids["front"], "views": {k: v for k, v in ids.items() if k != "front"},
                "views_consistent_confirmed": True}
        response = client.post("/api/jobs", json=body)
        assert response.status_code == 201, response.text
        job = app.state.store.job(response.json()["id"])
        received = {}

        def fake_cloud_step(_job, name, payload):
            if name == "geometry":
                received.update(payload)
            raise RuntimeError("Stop after inspecting payload")

        monkeypatch.setattr(app.state.pipeline, "cloud_step", fake_cloud_step)
        app.state.pipeline.run_job(job)
        assert {v["ViewType"] for v in received["MultiViewImages"]} == set(body["views"])
        assert received["Model"] == "3.1"


def test_old_model_rejects_diagonal_views(settings, image_bytes):
    old = settings.model_copy(update={"tencent_model": "3.0"})
    app = create_app(old, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        front = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()["id"]
        diagonal = client.post("/api/assets", files={"file": ("left-front.png", image_bytes)}).json()["id"]
        result = client.post("/api/jobs", json={"front": front, "views": {"left_front": diagonal},
                                                "views_consistent_confirmed": True})
        assert result.status_code == 422
        assert "3.1" in result.text

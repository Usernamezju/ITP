import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from itp.api import create_app
from itp.config import Settings
from itp.schemas import JobRequest


def client_for(settings):
    return TestClient(create_app(settings, start_worker=False), base_url="http://localhost:8000")


def test_empty_configuration_allows_upload_but_never_submits(tmp_path, image_bytes):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with client_for(settings) as client:
        caps = client.get("/api/capabilities").json()
        assert not caps["geometry"] and not caps["pose"]
        upload = client.post("/api/assets", files={"file": ("character.png", image_bytes)})
        assert upload.status_code == 201
        asset = upload.json()
        assert "filename" not in asset
        assert client.get(asset["url"]).status_code == 200
        result = client.post("/api/jobs", json={"front": asset["id"]})
        assert result.status_code == 503
        assert client.get("/api/jobs").json() == []


def test_upload_validation_and_no_exif(settings, image_bytes):
    with client_for(settings) as client:
        assert (
            client.post("/api/assets", files={"file": ("fake.png", b"not an image")}).status_code
            == 422
        )
        out = io.BytesIO()
        Image.new("RGB", (10, 10)).save(out, format="PNG")
        assert (
            client.post("/api/assets", files={"file": ("small.png", out.getvalue())}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/assets?remove_background=true", files={"file": ("a.png", image_bytes)}
            ).status_code
            == 503
        )
        upload = client.post("/api/assets", files={"file": ("../../evil.png", image_bytes)}).json()
        file = client.get(upload["url"])
        assert Image.open(io.BytesIO(file.content)).size == (256, 256)
        assert not (settings.data_dir / "evil.png").exists()


def test_local_origin_and_secret_redaction(settings, image_bytes):
    with client_for(settings) as client:
        assert (
            client.post(
                "/api/assets",
                headers={"Origin": "https://evil.example"},
                files={"file": ("a.png", image_bytes)},
            ).status_code
            == 403
        )
        assert "test-only" not in client.get("/api/capabilities").text
        assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


def test_public_origin_allows_proxied_browser_upload(settings, image_bytes):
    settings = settings.model_copy(update={"public_origin": "https://example.org:8443"})
    with client_for(settings) as client:
        assert client.post(
            "/api/assets",
            headers={"Origin": settings.public_origin},
            files={"file": ("a.png", image_bytes)},
        ).status_code == 201
        assert client.post(
            "/api/assets",
            headers={"Origin": "https://evil.example"},
            files={"file": ("a.png", image_bytes)},
        ).status_code == 403


def test_public_origin_requires_exact_https_origin():
    for origin in ("http://example.org", "https://example.org/path", "https://example.org/?x=1"):
        with pytest.raises(ValueError):
            Settings(_env_file=None, public_origin=origin)


def test_job_create_and_missing_asset(settings, image_bytes):
    with client_for(settings) as client:
        assert client.post("/api/jobs", json={"front": "0" * 32}).status_code == 422
        asset = client.post("/api/assets", files={"file": ("a.png", image_bytes)}).json()
        created = client.post("/api/jobs", json={"front": asset["id"]})
        assert created.status_code == 201
        assert created.json()["models"]["geometry"] == settings.tencent_model
        job_id = created.json()["id"]
        assert client.get(f"/api/jobs/{job_id}").json()["state"] == "queued"
        assert client.post(f"/api/jobs/{job_id}/review", json={"approve": True}).status_code == 409
        assert client.get("/api/assets/not-found/file").status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"pose_mode": "custom"},
        {"pose_mode": "a-pose", "views": {"left": "a" * 32}},
        {
            "pose_mode": "custom",
            "pose_reference": "a" * 32,
            "rig": True,
            "neutral_pose_confirmed": True,
        },
        {"rig": True},
        {"face_count": 1},
    ],
)
def test_invalid_workflows(overrides):
    with pytest.raises(ValueError):
        JobRequest(front="f" * 32, **overrides)


def test_foreign_endpoints_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, pose_endpoint="https://dashscope-intl.aliyuncs.com/anything")
    with pytest.raises(ValueError):
        Settings(_env_file=None, tencent_endpoint="hunyuan.intl.tencentcloudapi.com")


def test_high_detail_face_count_is_accepted():
    request = JobRequest(front="f" * 32, face_count=1_500_000)
    assert request.face_count == 1_500_000
    with pytest.raises(ValueError):
        JobRequest(front="f" * 32, face_count=1_500_001)


def test_web_settings_save_apply_and_hide_secrets(tmp_path):
    config_path = tmp_path / ".env"
    config_path.write_text("# keep this comment\nITP_DATA_DIR=./data\n", encoding="utf-8")
    settings = Settings(_env_file=None, data_dir=tmp_path / "assets")
    app = create_app(settings, start_worker=False, config_path=config_path)
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.patch(
            "/api/settings",
            json={
                "tencent_endpoint": "ai3d.tencentcloudapi.com",
                "tencent_region": "ap-guangzhou",
                "tencent_secret_id": "id-secret",
                "tencent_secret_key": 'key-secret$#"',
                "pose_endpoint": "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
                "multimodal-generation/generation",
                "pose_api_key": "pose-secret",
            },
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["tencent_secret_key_set"] is True
        assert 'key-secret$#"' not in response.text
        assert "pose-secret" not in client.get("/api/settings").text
        assert client.get("/api/capabilities").json()["geometry"] is True
        assert client.get("/api/capabilities").json()["pose"] is True
        assert config_path.read_text(encoding="utf-8").startswith("# keep this comment")
        assert config_path.stat().st_mode & 0o777 == 0o600
        restored = Settings(_env_file=config_path, data_dir=tmp_path / "assets")
        assert restored.tencent_secret_key.get_secret_value() == 'key-secret$#"'
        assert restored.pose_api_key.get_secret_value() == "pose-secret"
        assert client.patch("/api/settings", json={"tencent_model": "3.1"}).status_code == 200
        assert app.state.settings.tencent_secret_key.get_secret_value() == 'key-secret$#"'
        assert client.patch("/api/settings", json={"pose_api_key": ""}).json()[
            "pose_api_key_set"
        ] is False
        assert client.get("/api/capabilities").json()["pose"] is False


def test_flux_max_settings_are_independent_and_persisted(tmp_path, image_bytes):
    settings = Settings(_env_file=None, data_dir=tmp_path,
                        flux_endpoint="https://api.bfl.ai/v1/flux-2-pro",
                        flux_api_key="pro-test-secret")
    env_file = tmp_path / ".env"
    app = create_app(settings, start_worker=False, config_path=env_file)
    with TestClient(app, base_url="http://localhost:8000") as client:
        public = client.get("/api/settings").json()
        assert public["flux_max_endpoint"] == ""
        assert public["flux_max_model"] == "flux-2-max"
        assert public["flux_max_api_key_set"] is False
        asset = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        assert client.post("/api/tryons", json={
            "provider": "flux_max", "person": {"front": asset["id"]},
            "garment": {"front": asset["id"]},
        }).status_code == 503
        result = client.patch("/api/settings", json={
            "flux_max_endpoint": "https://api.bfl.ai/v1/flux-2-max",
            "flux_max_api_key": "max-test-secret",
        })
        assert result.status_code == 200
        assert result.json()["flux_max_api_key_set"] is True
        assert "max-test-secret" not in result.text
        assert "pro-test-secret" not in result.text
        restored = Settings(_env_file=env_file)
        assert restored.flux_max_api_key.get_secret_value() == "max-test-secret"
        assert restored.flux_max_endpoint == "https://api.bfl.ai/v1/flux-2-max"
        caps = client.get("/api/capabilities").json()["tryon_providers"]
        assert caps["flux"] is True and caps["flux_max"] is True
        created = client.post("/api/tryons", json={
            "provider": "flux_max", "person": {"front": asset["id"]},
            "garment": {"back": asset["id"]},
        })
        assert created.status_code == 201
        assert created.json()["model"] == "flux-2-max"
        assert created.json()["provider"] == "flux_max"
        for invalid in ("http://api.bfl.ai/v1/flux-2-max",
                        "https://api.bfl.ai/v1/flux-2-pro"):
            assert client.patch("/api/settings", json={
                "flux_max_endpoint": invalid,
            }).status_code == 422
        cleared = client.patch("/api/settings", json={"flux_max_api_key": ""})
        assert cleared.status_code == 200
        assert cleared.json()["flux_max_api_key_set"] is False
        assert app.state.settings.flux_api_key.get_secret_value() == "pro-test-secret"
        assert client.get("/api/capabilities").json()["tryon_providers"]["flux"] is True


def test_klein_9b_settings_rotate_secret_without_duplicate_env_keys(tmp_path):
    config_path = tmp_path / ".env"
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "assets"),
        start_worker=False, config_path=config_path,
    )
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.patch("/api/settings", json={
            "flux_klein_9b_endpoint": "http://127.0.0.1:8789/v1/flux-klein/edit",
            "flux_klein_9b_api_key": "old-private-key",
        }).status_code == 200
        response = client.patch("/api/settings", json={
            "flux_klein_9b_api_key": "new-private-key", "image_provider": "so",
        })
        assert response.status_code == 200
        assert response.json()["flux_klein_9b_api_key_set"] is True
        assert "new-private-key" not in response.text
        saved = config_path.read_text(encoding="utf-8")
        assert saved.count("ITP_FLUX_KLEIN_9B_API_KEY=") == 1
        assert "old-private-key" not in saved
        restored = Settings(_env_file=config_path)
        assert restored.flux_klein_9b_api_key.get_secret_value() == "new-private-key"
        caps = client.get("/api/capabilities").json()["tryon_providers"]
        assert caps["flux_klein_9b"] is True and caps["flux_klein"] is False
        assert client.patch("/api/settings", json={
            "flux_klein_9b_endpoint": "http://remote.example/v1/flux-klein/edit",
        }).status_code == 422


def test_web_settings_reject_invalid_values_and_foreign_origin(tmp_path, monkeypatch):
    config_path = tmp_path / ".env"
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "assets"),
        start_worker=False,
        config_path=config_path,
    )
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.patch(
            "/api/settings", json={"pose_api_key": "private"},
            headers={"Origin": "https://evil.example"},
        ).status_code == 404
        invalid = client.patch("/api/settings", json={
            "pose_endpoint": "https://evil.example/path", "pose_api_key": "private",
        })
        assert invalid.status_code == 422
        assert "private" not in invalid.text
        assert not config_path.exists()
        assert (
            client.patch("/api/settings", json={"pose_api_key": "line\nbreak"}).status_code
            == 422
        )
        too_long = "private-key-" * 100
        rejected = client.patch("/api/settings", json={"pose_api_key": too_long})
        assert rejected.status_code == 422
        assert too_long not in rejected.text
        monkeypatch.setenv("ITP_POSE_API_KEY", "provided-by-environment")
        assert client.patch("/api/settings", json={"pose_api_key": "private"}).status_code == 409
        assert not config_path.exists()

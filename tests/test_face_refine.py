import base64
import io
import struct

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from itp.api import create_app
from auth_helpers import fund_client
from itp.config import Settings
from itp.face_refine import REQUIRED_OPERATIONS, FaceVerseProvider, valid_glb


def face_settings(settings):
    return settings.model_copy(
        update={
            "faceverse_endpoint": "https://face.example.cn/v1/face-refine",
            "faceverse_api_key": SecretStr("test-only"),
        }
    )


def glb_bytes():
    chunk = b'{"asset":{"version":"2.0"}}'
    chunk += b" " * (-len(chunk) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 20 + len(chunk))
        + struct.pack("<I4s", len(chunk), b"JSON")
        + chunk
    )


def highres_photo():
    output = io.BytesIO()
    Image.new("RGB", (2048, 1536), "orange").save(output, format="JPEG")
    return output.getvalue()


def test_highres_face_photo_and_refinement_workflow(settings):
    app = create_app(face_settings(settings), start_worker=False)
    calls = []
    report = {"face_bbox": [200, 100, 800, 850], "operations": sorted(REQUIRED_OPERATIONS)}

    def fake_refine(mesh, photo, model):
        calls.append((mesh, photo, model))
        return glb_bytes(), report

    app.state.face_worker.provider.refine = fake_refine

    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        store = app.state.store
        owner = app.state.merchants.merchant_by_name("model-tester")["id"]
        mesh_id, mesh_path = store.new_asset_path("glb")
        mesh_path.write_bytes(glb_bytes())
        store.add_asset(mesh_id, mesh_path, "model", format="GLB", owner_id=owner)
        source = store.create_job({"name": "模型", "front": "f" * 32}, owner_id=owner)
        source["state"] = "succeeded"
        source["artifacts"].append(
            {
                "asset_id": mesh_id,
                "stage": "geometry",
                "format": "GLB",
                "index": 0,
            }
        )
        store.save_job(source)
        uploaded = client.post("/api/face-photos", files={"file": ("photo.jpg", highres_photo())})
        assert uploaded.status_code == 201, uploaded.text
        photo = uploaded.json()
        assert (photo["width"], photo["height"]) == (2048, 1536)
        created = client.post(
            f"/api/jobs/{source['id']}/face-refinement", json={"face_photo": photo["id"]}
        )
        assert created.status_code == 201, created.text
        item = created.json()
        assert (
            client.post(
                f"/api/jobs/{source['id']}/face-refinement", json={"face_photo": photo["id"]}
            ).status_code
            == 409
        )
        app.state.face_worker.run_job(item)
        ready = client.get(f"/api/jobs/{source['id']}/face-refinement").json()[0]
        assert ready["state"] == "ready"
        assert ready["report"] == report
        assert calls[0][2] == "faceverse-v4"
        assert client.get(f"/api/assets/{ready['result_asset']}/file").content == glb_bytes()
        artifacts = client.get(f"/api/jobs/{source['id']}").json()["artifacts"]
        assert artifacts[0]["asset_id"] == mesh_id
        assert artifacts[-1]["asset_id"] == ready["result_asset"]
        assert artifacts[-1]["stage"] == "face_refine"


def test_faceverse_provider_contract_and_secret(tmp_path, settings):
    received = {}
    mesh = tmp_path / "body.glb"
    mesh.write_bytes(glb_bytes())
    photo = tmp_path / "face.jpg"
    photo.write_bytes(highres_photo())
    report = {"face_bbox": [1, 2, 3, 4], "operations": sorted(REQUIRED_OPERATIONS)}

    def reply(request):
        received["auth"] = request.headers.get("Authorization")
        received["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "glb_base64": base64.b64encode(glb_bytes()).decode(),
                "report": report,
            },
        )

    provider = FaceVerseProvider(
        face_settings(settings), client=httpx.Client(transport=httpx.MockTransport(reply))
    )
    output, result_report = provider.refine(mesh, photo, "faceverse-v4")
    assert output == glb_bytes()
    assert result_report == report
    assert received["auth"] == "Bearer test-only"
    assert "test-only" not in received["body"]
    assert "mesh_glb_base64" in received["body"]
    assert "face_photo_base64" in received["body"]
    assert valid_glb(output)
    assert not valid_glb(b"glTF" + b"\x00" * 12)


def test_faceverse_endpoint_validation_and_independence(settings, image_bytes):
    for endpoint in ("http://public.example.cn/v1/face-refine", "https://public.example.cn/other"):
        try:
            Settings(_env_file=None, faceverse_endpoint=endpoint)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe endpoint accepted: {endpoint}")
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        asset = client.post("/api/assets", files={"file": ("person.png", image_bytes)}).json()
        assert client.get("/api/capabilities").json()["faceverse"] is False
        assert client.post("/api/jobs", json={"front": asset["id"]}).status_code == 201


def test_faceverse_settings_can_be_saved_without_exposing_token(tmp_path):
    config_path = tmp_path / ".env"
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data"),
        start_worker=False,
        config_path=config_path,
    )
    with TestClient(app, base_url="http://localhost:8000") as client:
        updated = client.patch(
            "/api/settings",
            json={
                "faceverse_endpoint": "https://face.example.cn/v1/face-refine",
                "faceverse_model": "faceverse-v4",
                "faceverse_api_key": "private-test-token",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["faceverse_api_key_set"] is True
        assert "private-test-token" not in updated.text
        assert "private-test-token" not in client.get("/api/capabilities").text
        assert client.get("/api/capabilities").json()["faceverse"] is True
        restored = Settings(_env_file=config_path, data_dir=tmp_path / "data")
        assert restored.faceverse_api_key.get_secret_value() == "private-test-token"

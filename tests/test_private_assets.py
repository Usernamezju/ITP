import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from auth_helpers import fund_client
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from test_commerce import triangle_glb
from test_customer_data import switch_to_second_account

from itp.api import create_app
from itp.private_assets import PrivateAssets


def test_restart_encrypted_restore_and_account_isolation(settings, image_bytes):
    settings.jwt_secret = SecretStr("private-restart-" + "x" * 32)
    app = create_app(settings, start_worker=False)
    with fund_client(TestClient(app, base_url="http://localhost:8000")) as client:
        token = client.headers["Authorization"]
        record = {"height_cm": 182, "private_marker": "secret-body-measurements"}
        assert (
            client.post(
                "/api/account/storage/records/body-profile", data={"value": json.dumps(record)}
            ).status_code
            == 200
        )
        uploaded = client.post("/api/assets", files={"file": ("a.png", image_bytes)}).json()
        asset_id = uploaded["id"]
        route = f"/api/account/storage/asset/{asset_id}/file"
        assert client.get(route).status_code == 200
        switch_to_second_account(client)
        assert client.get(route).status_code == 403
        assert client.get("/api/account/storage/record/body-profile").status_code == 403
        assert client.get("/api/account/storage").json()["items"] == []
    vault = PrivateAssets(settings)
    with sqlite3.connect(vault.db) as conn:
        documents = b"".join(row[0] for row in conn.execute("SELECT document FROM objects"))
    assert b"secret-body-measurements" not in documents
    for path in vault.objects.iterdir():
        assert not path.read_bytes().startswith(b"\x89PNG")
        assert path.stat().st_mode & 0o777 == 0o600
    restarted = create_app(settings, start_worker=False)
    with TestClient(
        restarted, base_url="http://localhost:8000", headers={"Authorization": token}
    ) as client:
        assert client.get("/api/account/storage/record/body-profile").json() == record
        assert client.get(route).content.startswith(b"\x89PNG")
        assert client.get(route).headers["cache-control"] == "no-store"
        assert (
            client.get(f"/private/objects/{next(vault.objects.iterdir()).name}").content
            != client.get(route).content
        )
        assert client.delete("/api/account/storage").status_code == 200
        assert client.get("/api/account/storage").json()["items"] == []
    assert list(vault.objects.iterdir()) == []


def test_validation_exif_quota_retention_and_orphans(settings):
    vault = PrivateAssets(settings)
    image = Image.new("RGB", (512, 512), "blue")
    source = io.BytesIO()
    exif = image.getexif()
    exif[0x010E] = "GPS secret marker"
    image.save(source, "JPEG", exif=exif)
    vault.put("a", "photo", {"kind": "face_photo"}, source.getvalue(), kind="face_photo")
    _, data, mime = vault.get("a", "photo")
    assert mime == "image/png" and b"GPS secret marker" not in data
    with pytest.raises(ValueError):
        vault.put("a", "../shared", {})
    with pytest.raises(ValueError):
        vault.put("a", "fake", {}, b"fake glb", kind="model")
    vault.put("a", "model", {"kind": "model"}, triangle_glb(), kind="model")
    vault.quota = 100
    with ThreadPoolExecutor(max_workers=4) as pool:

        def upload(i):
            with pytest.raises(ValueError):
                vault.put("a", f"too-big-{i}", {"large": "x" * 100})

        list(pool.map(upload, range(4)))
    orphan = vault.objects / ("c" * 32)
    orphan.write_bytes(b"encrypted-orphan")
    assert vault.cleanup()["orphan_files"] == 1
    assert not orphan.exists()
    result = vault.cleanup(now=10**11)
    assert result["expired"] == 2
    assert not list(vault.objects.iterdir())


def test_missing_key_fails_closed_and_legacy_data_untouched(settings):
    legacy = settings.data_dir / "assets"
    legacy.mkdir(parents=True)
    sentinel = legacy / "shared.png"
    sentinel.write_bytes(b"legacy-unowned")
    vault = PrivateAssets(settings)
    vault.put("a", "body-profile", {"height_cm": 172})
    key = settings.data_dir.parent / (settings.data_dir.name + ".keys") / "customer-assets.key"
    key.unlink()
    with pytest.raises(RuntimeError, match="key is missing"):
        PrivateAssets(settings)
    assert sentinel.read_bytes() == b"legacy-unowned"


def test_encrypted_backup_has_consistent_index_and_excludes_key(settings, tmp_path):
    from scripts.private_assets import backup

    vault = PrivateAssets(settings)
    vault.put("owner", "model", {"kind": "model"}, triangle_glb(), kind="model")
    directory = backup(vault, tmp_path / "backups")
    with sqlite3.connect(directory / "index.sqlite3") as conn:
        key = conn.execute("SELECT object_key FROM objects").fetchone()[0]
    assert vault.cipher.decrypt((directory / "objects" / key).read_bytes()) == triangle_glb()
    assert not list(directory.rglob("*.key"))


def test_delete_cancels_and_refunds_running_work_without_recreating_archive(settings, image_bytes):
    app = create_app(settings, start_worker=False)
    with fund_client(TestClient(app, base_url="http://localhost:8000")) as client:
        asset = client.post("/api/assets", files={"file": ("input.png", image_bytes)}).json()
        before = client.get("/api/account/points").json()["balance_points"]
        job = client.post("/api/jobs", json={"name": "delete-running", "front": asset["id"]}).json()
        assert client.get("/api/account/points").json()["balance_points"] == before - 800
        assert client.delete("/api/account/storage").status_code == 200
        assert client.get("/api/account/points").json()["balance_points"] == before
        assert client.get("/api/jobs").json() == []
        current = dict(job, state="failed")
        with app.state.store.processing(
            job["id"], app.state.merchants.merchant_by_name("model-tester")["id"]
        ):
            app.state.store.save_job(current)
        assert client.get("/api/account/storage").json()["items"] == []


def test_final_model_archived_after_browser_acknowledgement(settings, image_bytes):
    from test_pipeline import Cloud

    app = create_app(settings, start_worker=False)
    with fund_client(TestClient(app, base_url="http://localhost:8000")) as client:
        asset = client.post("/api/assets", files={"file": ("input.png", image_bytes)}).json()
        job = client.post(
            "/api/jobs",
            json={
                "name": "durable-model",
                "front": asset["id"],
                "pose_mode": "original",
                "topology": False,
                "texture": False,
                "rig": False,
            },
        ).json()
        app.state.pipeline.cloud = Cloud()
        app.state.pipeline.fetch = lambda url, path, **_: path.write_bytes(triangle_glb())
        app.state.pipeline.run_job(app.state.store.job(job["id"]))
        finished = client.get(f"/api/jobs/{job['id']}").json()
        assert finished["state"] == "succeeded"
        assert client.post(f"/api/jobs/{job['id']}/acknowledge").status_code == 204
        archived = client.get(f"/api/account/storage/job/{job['id']}")
        assert archived.status_code == 200
        model = archived.json()["artifacts"][0]["asset_id"]
        assert client.get(f"/api/account/storage/asset/{model}/file").content == triangle_glb()
        assert "results" not in json.dumps(archived.json()["steps"])

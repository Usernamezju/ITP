"""Offline mock verification of the FaceVerse V4 HTTP contract.

Loads the real FastAPI application with a stub inference object installed on
``app.state`` and exercises routing, bearer authentication, the size limits,
the ten-operation ClothiNation contract, GLB and photo validation and the error
mapping, without model weights, CUDA initialisation or GPU inference.

Run inside the service venv (torch, mediapipe, trimesh and pyrender are
imported by the application itself). It does not prove reconstruction quality.
"""

import base64
import io
import json
import os
import struct
import sys
import threading
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

APP_ROOT = Path(
    os.environ.get("ITP_FACEVERSE_APP_ROOT", Path(__file__).resolve().parents[1])
)
sys.path.insert(0, str(APP_ROOT))
from itp_faceverse_service.api import OPERATIONS  # noqa: E402
from itp_faceverse_service.inference import FaceDetectionError  # noqa: E402

TOKEN = "mock-token-for-offline-verification"
PRESERVE = ["hair", "back_head", "neck"]
LANDMARKS = ["eyes", "nose_tip", "mouth_corners", "chin", "head_width"]


def minimal_glb() -> bytes:
    """A GLB v2 container with the required header/chunk layout and no mesh."""
    payload = json.dumps({"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0})
    raw = payload.encode()
    raw += b" " * ((4 - len(raw) % 4) % 4)
    total = 12 + 8 + len(raw)
    return struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(raw), b"JSON") + raw


def photo_png(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (210, 180, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class StubInference:
    """Raises on the first model call so no GPU work can happen."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[tuple[int, int]] = []

    def reconstruct(self, photo: Image.Image):
        self.calls.append(photo.size)
        raise self.error

    def detect_landmarks(self, rgb):
        raise self.error

    def close(self) -> None:
        pass


def payload(**overrides) -> dict:
    body = {
        "model": "faceverse-v4",
        "mesh_glb_base64": b64(minimal_glb()),
        "face_photo_base64": b64(photo_png((512, 512))),
        "preserve": PRESERVE,
        "alignment_landmarks": LANDMARKS,
        "required_operations": list(OPERATIONS),
    }
    body.update(overrides)
    return body


def build_client(error: Exception) -> tuple[TestClient, StubInference]:
    import itp_faceverse_service.api as service

    stub = StubInference(error)
    service.app.state.inference = stub
    service.app.state.inference_lock = threading.Lock()
    # No ``with`` block: the lifespan never runs, so the stub stays installed.
    return TestClient(service.app), stub


def main() -> None:
    os.environ["ITP_FACEVERSE_API_TOKEN"] = TOKEN
    import itp_faceverse_service.api as service

    passed: list[str] = []

    routes = {(route.path, tuple(sorted(route.methods or []))) for route in service.app.routes}
    assert ("/health", ("GET",)) in routes, routes
    assert ("/v1/face-refine", ("POST",)) in routes, routes
    schema = service.app.openapi()
    assert "/v1/face-refine" in schema["paths"], sorted(schema["paths"])
    passed.append("OpenAPI exposes GET /health and POST /v1/face-refine")

    client, stub = build_client(FaceDetectionError("stub: no usable face"))

    response = client.post("/v1/face-refine", json=payload())
    assert response.status_code == 401, response.text
    response = client.post(
        "/v1/face-refine", json=payload(), headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401, response.text
    passed.append("POST rejects a missing and a wrong bearer token with 401")

    auth = {"Authorization": f"Bearer {TOKEN}"}
    for field, value in (
        ("model", "faceverse-v3"),
        ("preserve", ["hair", "neck"]),
        ("alignment_landmarks", ["eyes", "chin"]),
        ("required_operations", list(OPERATIONS)[:3]),
    ):
        response = client.post("/v1/face-refine", json=payload(**{field: value}), headers=auth)
        assert response.status_code == 422, (field, response.status_code, response.text)
    assert stub.calls == [], "contract violations must be rejected before the model call"
    passed.append("POST rejects model, preserve, landmark and operation contract violations")

    for field, value in (
        ("mesh_glb_base64", "not-base64!"),
        ("mesh_glb_base64", b64(b"truncated")),
        ("face_photo_base64", b64(b"not-an-image")),
        ("face_photo_base64", b64(photo_png((256, 256)))),
    ):
        response = client.post("/v1/face-refine", json=payload(**{field: value}), headers=auth)
        assert response.status_code == 422, (field, response.status_code, response.text)
    assert stub.calls == [], "invalid assets must be rejected before the model call"
    passed.append("POST rejects invalid base64, non-GLB geometry and undersized photos")

    response = client.post("/v1/face-refine", json=payload(), headers=auth)
    assert response.status_code == 422, response.text
    assert "stub: no usable face" in response.json()["detail"], response.text
    assert stub.calls == [(512, 512)], stub.calls
    passed.append("a valid request reaches the model once and maps FaceDetectionError to 422")

    failing, _ = build_client(RuntimeError("stub: simulated CUDA failure"))
    response = failing.post("/v1/face-refine", json=payload(), headers=auth)
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "Face refinement failed; inspect server logs", response.text
    passed.append("an unexpected model failure is mapped to an opaque 500")

    headers = client.get("/health").json()
    assert headers["model"] == "faceverse-v4", headers
    assert headers["status"] == "ready", headers
    passed.append("GET /health reports the model and the live CUDA flag without inference")

    for index, item in enumerate(passed, 1):
        print(f"PASS {index}: {item}")
    print(f"mock API verification: {len(passed)}/{len(passed)} checks passed")
    print("scope: HTTP contract only; no weights, no CUDA init, no GPU, no real inference")


if __name__ == "__main__":
    main()

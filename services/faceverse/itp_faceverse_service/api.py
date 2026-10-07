"""Authenticated ClothiNation v1 FaceVerse V4 face-refinement HTTP service."""

import base64
import binascii
import hmac
import io
import logging
import os
import struct
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import torch
import trimesh
from fastapi import FastAPI, Header, HTTPException, Request
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict

from .fusion import fuse
from .geometry import (
    GeometryError,
    flatten_glb,
    locate_face,
    remove_face_region,
    similarity_alignment,
    source_patch,
)
from .inference import FaceDetectionError, FaceVerseInference

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("ITP_FACEVERSE_LOG_LEVEL", "INFO"))

OPERATIONS = (
    "face_detection",
    "faceverse_reconstruction",
    "face_region_removal",
    "similarity_alignment",
    "boundary_matching",
    "laplacian_deformation",
    "remesh",
    "vertex_welding",
    "texture_fusion",
    "collision_check",
)
PRESERVE = {"hair", "back_head", "neck"}
LANDMARK_GROUPS = {"eyes", "nose_tip", "mouth_corners", "chin", "head_width"}
MAX_BODY = 100 * 1024 * 1024
MAX_PHOTO = 10 * 1024 * 1024
MAX_BODY_JSON = 155 * 1024 * 1024


class RefineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    mesh_glb_base64: str
    face_photo_base64: str
    preserve: list[str]
    alignment_landmarks: list[str]
    required_operations: list[str]


def _decode_base64(value: str, max_size: int, label: str) -> bytes:
    if len(value) > (max_size * 4 // 3 + 8):
        raise ValueError(f"{label} exceeds size limit")
    try:
        data = base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise ValueError(f"{label} is not valid base64") from exc
    if not data or len(data) > max_size:
        raise ValueError(f"{label} is empty or too large")
    return data


def _validate_glb(data: bytes) -> None:
    if len(data) < 20:
        raise ValueError("Input GLB is truncated")
    magic, version, declared = struct.unpack_from("<4sII", data)
    json_size, json_type = struct.unpack_from("<I4s", data, 12)
    if (magic, version, declared, json_type) != (
        b"glTF",
        2,
        len(data),
        b"JSON",
    ) or 20 + json_size > len(data):
        raise ValueError("Input is not a valid GLB v2 container")


def _decode_photo(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Photo must be PNG, JPEG, or WebP")
            if source.width * source.height > 25_000_000 or min(source.size) < 512:
                raise ValueError("Photo resolution must be at least 512 px and at most 25 MP")
            return ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Photo cannot be decoded") from exc


def _validate_contract(payload: RefineRequest) -> None:
    if payload.model != "faceverse-v4":
        raise ValueError("Only model 'faceverse-v4' is available")
    if set(payload.preserve) != PRESERVE:
        raise ValueError("ClothiNation hair, back_head and neck preservation is required")
    if set(payload.alignment_landmarks) != LANDMARK_GROUPS:
        raise ValueError("ClothiNation eye, nose, mouth, chin and head-width landmarks are required")
    if set(payload.required_operations) != set(OPERATIONS):
        raise ValueError("ClothiNation v1 requires all ten face-refinement operations")


def _run_refinement(inference: FaceVerseInference, payload: RefineRequest) -> dict:
    _validate_contract(payload)
    mesh_data = _decode_base64(payload.mesh_glb_base64, MAX_BODY, "GLB")
    photo_data = _decode_base64(payload.face_photo_base64, MAX_PHOTO, "Photo")
    _validate_glb(mesh_data)
    photo = _decode_photo(photo_data)
    source = inference.reconstruct(photo)
    body = flatten_glb(mesh_data)
    located = locate_face(body, inference)
    scale, rotation, translation, residual = similarity_alignment(
        source.landmarks_3d, located.landmarks
    )
    head_width = float(
        np.linalg.norm(located.landmarks["head_left"] - located.landmarks["head_right"])
    )
    if residual > head_width * 0.25:
        raise GeometryError("Face and body landmarks are too inconsistent to fuse safely")
    kept, target_boundary, removed = remove_face_region(located)
    patch = source_patch(source, scale, rotation, translation)
    output, quality = fuse(kept, patch, target_boundary, head_width)
    exported = output.export(file_type="glb")
    _validate_glb(exported)
    reloaded = trimesh.load(io.BytesIO(exported), file_type="glb", force="scene")
    if not reloaded.geometry:
        raise GeometryError("Exported GLB contains no geometry")
    report = {
        "face_bbox": list(source.bbox),
        "operations": list(OPERATIONS),
        "landmarks": {
            "source_photo_px": {
                name: value.tolist() for name, value in source.landmarks_2d.items()
            },
            "body_world": {name: value.tolist() for name, value in located.landmarks.items()},
            "scale": scale,
            "rotation": rotation.tolist(),
            "translation": translation.tolist(),
        },
        "quality": {
            "alignment_rms": residual,
            "head_width": head_width,
            "alignment_rms_ratio": residual / head_width,
            "boundary_rms": quality.boundary_rms,
            "boundary_max": quality.boundary_max,
            "collision_count": quality.collision_count,
            "collision_sampled_vertices": quality.sampled_vertices,
            "collision_method": "nearest_surface_signed_normal_screening",
            "welded_vertices": quality.welded_vertices,
            "bridge_faces": quality.bridge_faces,
            "repaired_seam_loops": quality.repaired_seam_loops,
            "open_boundary_edges": quality.open_boundary_edges,
            "face_triangles": quality.face_triangles,
            "removed_body_triangles": int(np.count_nonzero(removed)),
            "output_triangles": int(len(output.faces)),
            "selected_body_view": located.view_index,
        },
    }
    logger.info("Refinement complete: %s triangles, %.4g landmark RMS", len(output.faces), residual)
    return {"glb_base64": base64.b64encode(exported).decode("ascii"), "report": report}


@asynccontextmanager
async def lifespan(app: FastAPI):
    upstream = Path(
        os.getenv(
            "ITP_FACEVERSE_VENDOR_ROOT",
            Path(__file__).resolve().parents[2] / "vendor" / "FaceVerse_v4",
        )
    )
    app.state.inference = FaceVerseInference(upstream, upstream / "data")
    app.state.inference_lock = threading.Lock()
    try:
        yield
    finally:
        app.state.inference.close()


app = FastAPI(title="ClothiNation FaceVerse V4 Refinement", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health(request: Request) -> dict:
    return {
        "status": "ready",
        "model": "faceverse-v4",
        "cuda": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }


@app.post("/v1/face-refine")
async def face_refine(request: Request, authorization: str | None = Header(default=None)) -> dict:
    token = os.getenv("ITP_FACEVERSE_API_TOKEN", "")
    if token and (not authorization or not hmac.compare_digest(authorization, f"Bearer {token}")):
        raise HTTPException(
            status_code=401, detail="Invalid bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_JSON:
        raise HTTPException(status_code=413, detail="Request exceeds size limit")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_JSON:
            raise HTTPException(status_code=413, detail="Request exceeds size limit")
    try:
        payload = RefineRequest.model_validate_json(body)
        with request.app.state.inference_lock:
            return _run_refinement(request.app.state.inference, payload)
    except (ValueError, FaceDetectionError, GeometryError) as exc:
        logger.warning("Refinement rejected: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        logger.exception("Face refinement failed")
        raise HTTPException(
            status_code=500, detail="Face refinement failed; inspect server logs"
        ) from None

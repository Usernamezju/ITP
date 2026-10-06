"""Remote FaceVerse reconstruction and mesh-fusion contract for completed GLB jobs."""

import base64
import binascii
import io
import json
import logging
import sqlite3
import struct
import threading
import time
import warnings
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from itp.config import Settings
from itp.preprocessing import MAX_UPLOAD
from itp.storage import Store
from itp.transient import TransientDocuments

logger = logging.getLogger(__name__)
MAX_GLB = 100 * 1024 * 1024
REQUIRED_OPERATIONS = {
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
}


def valid_glb(data: bytes) -> bool:
    if len(data) < 20:
        return False
    magic, version, declared = struct.unpack_from("<4sII", data)
    chunk_length, chunk_type = struct.unpack_from("<I4s", data, 12)
    return (
        magic == b"glTF"
        and version == 2
        and declared == len(data)
        and chunk_type == b"JSON"
        and 20 + chunk_length <= len(data)
        and chunk_length % 4 == 0
    )


class FaceRefineRequest(BaseModel):
    """One refinement run: the model to refine and the photo to refine it from.

    Both are temporary uploads made for this run; the browser keeps its own copy
    of the original model and hands it back whenever a refinement is wanted.
    """

    model_config = ConfigDict(extra="forbid")
    mesh: str = Field(pattern=r"^[a-f0-9]{32}$")
    face_photo: str = Field(pattern=r"^[a-f0-9]{32}$")


def prepare_face_photo(data: bytes) -> Image.Image:
    """Normalize an original photo without the usual 1024px geometry-input downscale."""
    if len(data) > MAX_UPLOAD:
        raise ValueError("高清照片不能超过 10 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("高清照片仅支持 PNG、JPEG、WebP")
                if min(source.size) < 512 or source.width * source.height > 25_000_000:
                    raise ValueError("高清照片短边至少 512 像素、总像素不超过 2500 万")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("不支持动态图像")
                image = ImageOps.exif_transpose(source).convert("RGB")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("高清照片损坏、格式不支持或尺寸过大") from exc
    image.info.clear()
    return image


class FaceRefineStore:
    """Legacy on-disk refinements. The running service keeps these in RAM only.

    Rows written before the switch name its second column ``source_job_id``;
    the statements here address columns by position, so both files keep working.
    """

    def __init__(self, root: Path):
        self.db = root / "face_refinements.sqlite3"
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS face_refinements "
                "(id TEXT PRIMARY KEY, mesh_asset TEXT, state TEXT, created REAL, document TEXT)"
            )

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def save(self, item: dict):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO face_refinements VALUES (?, ?, ?, ?, ?)",
                (
                    item["id"],
                    item["mesh_asset"],
                    item["state"],
                    item["created"],
                    json.dumps(item),
                ),
            )

    def create(self, mesh_asset: str, face_photo: str, model: str, owner_id=None) -> dict:
        item = {
            "id": uuid4().hex,
            "face_photo": face_photo,
            "mesh_asset": mesh_asset,
            "model": model,
            "state": "queued",
            "created": time.time(),
            "result_asset": None,
            "report": None,
            "error": None,
            "owner_id": owner_id,
        }
        self.save(item)
        return item

    def get(self, item_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT document FROM face_refinements WHERE id = ?", (item_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def active(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT document FROM face_refinements WHERE state IN "
                "('queued', 'running', 'submitting') ORDER BY created"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]


class FaceVerseProvider:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=600, follow_redirects=False)

    def refine(self, mesh: Path, photo: Path, model: str) -> tuple[bytes, dict]:
        if not self.settings.faceverse_ready:
            raise RuntimeError("FaceVerse 服务器尚未配置")
        mesh_bytes = mesh.read_bytes()
        photo_bytes = photo.read_bytes()
        if not valid_glb(mesh_bytes) or len(mesh_bytes) > MAX_GLB:
            raise ValueError("输入 GLB 无效或超过 100 MiB")
        if len(photo_bytes) > MAX_GLB:
            raise ValueError("高清照片资产过大")
        payload = {
            "model": model,
            "mesh_glb_base64": base64.b64encode(mesh_bytes).decode("ascii"),
            "face_photo_base64": base64.b64encode(photo_bytes).decode("ascii"),
            "preserve": ["hair", "back_head", "neck"],
            "alignment_landmarks": ["eyes", "nose_tip", "mouth_corners", "chin", "head_width"],
            "required_operations": sorted(REQUIRED_OPERATIONS),
        }
        headers = {}
        key = self.settings.faceverse_api_key.get_secret_value()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            response = self.client.post(
                self.settings.faceverse_endpoint, json=payload, headers=headers
            )
            response.raise_for_status()
            if len(response.content) > 140 * 1024 * 1024:
                raise ValueError("FaceVerse 返回体过大")
            result = response.json()
            glb = base64.b64decode(result["glb_base64"], validate=True)
            report = result["report"]
        except (httpx.HTTPError, KeyError, ValueError, binascii.Error) as exc:
            raise RuntimeError(
                f"FaceVerse 远程精修失败（{type(exc).__name__}）；请检查服务器日志"
            ) from None
        if not valid_glb(glb) or len(glb) > MAX_GLB:
            raise ValueError("FaceVerse 返回的 GLB 无效或超过 100 MiB")
        if not isinstance(report, dict) or not REQUIRED_OPERATIONS.issubset(
            set(report.get("operations", []))
        ):
            raise ValueError("FaceVerse 服务未报告完整的人脸重建与融合步骤")
        if not isinstance(report.get("face_bbox"), list) or len(report["face_bbox"]) != 4:
            raise ValueError("FaceVerse 服务未返回人脸检测框")
        return glb, report


class FaceRefineWorker:
    def __init__(
        self,
        assets: Store,
        jobs: TransientDocuments,
        settings: Settings,
        provider: FaceVerseProvider | None = None,
    ):
        self.assets, self.jobs, self.settings = assets, jobs, settings
        self.provider = provider or FaceVerseProvider(settings)
        self.stop = threading.Event()

    def run_job(self, item: dict):
        with self.assets.processing(item["id"], item.get("owner_id")):
            self._run_job(item)

    def _run_job(self, item: dict):
        item["state"] = "submitting"
        self.jobs.save(item)
        try:
            glb, report = self.provider.refine(
                self.assets.path(item["mesh_asset"]),
                self.assets.path(item["face_photo"]),
                item["model"],
            )
            asset_id, path = self.assets.new_asset_path("glb")
            path.write_bytes(glb)
            self.assets.add_asset(asset_id, path, "model", format="GLB")
            # The refined model is delivered on its own; the browser owns the
            # original and never needed the server to keep a job for this.
            item["result_asset"] = asset_id
            item["report"] = report
            item["state"] = "ready"
            self.jobs.save(item)
        except Exception as exc:
            item["state"] = "failed"
            item["error"] = (
                str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "脸部精细建模失败"
            )
            self.jobs.save(item)
            logger.warning("Face refinement %s failed (%s)", item["id"], type(exc).__name__)
        finally:
            if item["state"] in {"ready", "failed", "cancelled"}:
                keep = [item["result_asset"]] if item["result_asset"] else []
                self.assets.finish(item["id"], keep)

    def run_forever(self):
        for item in self.jobs.active():
            if item["state"] in {"running", "submitting"}:
                item["state"] = "failed"
                item["error"] = "进程中断时远程结果不确定；未自动重提，请检查服务器日志"
                self.jobs.save(item)
        while not self.stop.is_set():
            for item in self.jobs.active():
                if item["state"] == "queued":
                    self.run_job(item)
            self.stop.wait(0.5)

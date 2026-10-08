"""Encrypted, account-scoped durable assets, separate from processing workspaces."""

import io
import json
import os
import re
import sqlite3
import time
from typing import Annotated
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps

from itp.face_refine import valid_glb
from itp.merchant_auth import current_user

KEY = re.compile(r"^[A-Za-z0-9:_-]{1,160}$")
MAX_MODEL = 150 * 1024 * 1024
MAX_IMAGE = 10 * 1024 * 1024


def normalize_blob(data, kind):
    if kind == "model":
        if len(data) > MAX_MODEL or not valid_glb(data):
            raise ValueError("模型必须是有效的 GLB，最大 150 MiB")
        return data, "model/gltf-binary"
    if len(data) > MAX_IMAGE:
        raise ValueError("图片不能超过 10 MiB")
    try:
        with Image.open(io.BytesIO(data)) as source:
            minimum = 512 if kind == "face_photo" else 64
            if (
                source.format not in {"PNG", "JPEG", "WEBP"}
                or min(source.size) < minimum
                or source.width * source.height > 25_000_000
                or getattr(source, "n_frames", 1) != 1
            ):
                raise ValueError("图片格式或尺寸无效")
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.info.clear()
            target = io.BytesIO()
            image.save(target, format="PNG")
            data = target.getvalue()
            if len(data) > 100 * 1024 * 1024:
                raise ValueError("解码后的图片过大")
            return data, "image/png"
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片内容无效") from exc


class PrivateAssets:
    def __init__(self, settings):
        self.root = settings.data_dir.resolve() / "private"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.objects = self.root / "objects"
        self.objects.mkdir(mode=0o700, exist_ok=True)
        self.db = self.root / "index.sqlite3"
        key_dir = settings.data_dir.resolve().parent / (settings.data_dir.name + ".keys")
        key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        key_dir.chmod(0o700)
        key_file = key_dir / "customer-assets.key"
        if not key_file.exists():
            if self.db.exists():
                raise RuntimeError(
                    "Private asset encryption key is missing; restore the isolated key"
                )
            try:
                fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as target:
                    target.write(Fernet.generate_key())
            except FileExistsError:
                pass
        key_file.chmod(0o600)
        self.cipher = Fernet(key_file.read_bytes())
        self.quota = settings.private_asset_quota_mb * 1024 * 1024
        self.retention = settings.private_asset_retention_days * 86400
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects (
                    owner TEXT NOT NULL, category TEXT NOT NULL, id TEXT NOT NULL,
                    object_key TEXT, size INTEGER NOT NULL, updated REAL NOT NULL,
                    document BLOB NOT NULL, PRIMARY KEY(owner,category,id)
                );
                CREATE INDEX IF NOT EXISTS objects_updated ON objects(updated);
            """)
        self.db.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.db, timeout=30)

    def put(self, owner, item_id, value, blob=None, *, category="record", kind=None):
        if not KEY.fullmatch(item_id) or category not in {
            "record",
            "asset",
            "job",
            "tryon",
            "face",
        }:
            raise ValueError("私有数据标识无效")
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > 256 * 1024:
            raise ValueError("记录不能超过 256 KiB")
        mime = None
        if blob is not None:
            blob, mime = normalize_blob(blob, kind)
        document = self.cipher.encrypt(json.dumps({"value": value, "mime": mime}).encode())
        size = len(document) + (len(blob) if blob is not None else 0)
        object_key = uuid4().hex if blob is not None else None
        old_key = None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT object_key,size FROM objects WHERE owner=? AND category=? AND id=?",
                (owner, category, item_id),
            ).fetchone()
            used, count = conn.execute(
                "SELECT COALESCE(SUM(size),0),COUNT(*) FROM objects WHERE owner=?", (owner,)
            ).fetchone()
            if used - (old[1] if old else 0) + size > self.quota or (not old and count >= 2000):
                raise ValueError("账号私有空间已满，请删除不再需要的资产")
            if old:
                old_key = old[0]
            if object_key:
                path = self.objects / object_key
                try:
                    with path.open("xb") as target:
                        path.chmod(0o600)
                        target.write(self.cipher.encrypt(blob))
                    conn.execute(
                        "INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)",
                        (owner, category, item_id, object_key, size, time.time(), document),
                    )
                    conn.commit()
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)",
                    (owner, category, item_id, None, size, time.time(), document),
                )
        if old_key:
            (self.objects / old_key).unlink(missing_ok=True)

    def get(self, owner, item_id, category="record"):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT object_key,updated,document FROM objects "
                "WHERE owner=? AND category=? AND id=?",
                (owner, category, item_id),
            ).fetchone()
            if not row:
                other = conn.execute(
                    "SELECT 1 FROM objects WHERE category=? AND id=?", (category, item_id)
                ).fetchone()
                raise HTTPException(
                    403 if other else 404, "无权访问该私有数据" if other else "私有数据不存在"
                )
        if row[1] < time.time() - self.retention:
            raise HTTPException(404, "私有数据已超过保留期限")
        decoded = json.loads(self.cipher.decrypt(row[2]))
        blob = self.cipher.decrypt((self.objects / row[0]).read_bytes()) if row[0] else None
        return decoded["value"], blob, decoded["mime"]

    def manifest(self, owner):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT category,id,object_key,updated,document FROM objects "
                "WHERE owner=? AND updated>=? ORDER BY updated",
                (owner, time.time() - self.retention),
            ).fetchall()
            used = conn.execute(
                "SELECT COALESCE(SUM(size),0) FROM objects WHERE owner=?", (owner,)
            ).fetchone()[0]
        return {
            "used_bytes": used,
            "quota_bytes": self.quota,
            "retention_days": self.retention // 86400,
            "items": [
                {
                    "category": r[0],
                    "id": r[1],
                    "has_blob": bool(r[2]),
                    "updated": r[3],
                    "value": json.loads(self.cipher.decrypt(r[4]))["value"],
                }
                for r in rows
            ],
        }

    def delete(self, owner, item_id=None):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            condition, args = (
                ("owner=?", (owner,)) if item_id is None else ("owner=? AND id=?", (owner, item_id))
            )
            keys = conn.execute(
                f"SELECT object_key FROM objects WHERE {condition}", args
            ).fetchall()
            conn.execute(f"DELETE FROM objects WHERE {condition}", args)
        for (key,) in keys:
            if key:
                (self.objects / key).unlink(missing_ok=True)

    def cleanup(self, now=None):
        now = time.time() if now is None else now
        # Keep the DB write lock throughout orphan detection to protect concurrent writers.
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            expired = conn.execute(
                "DELETE FROM objects WHERE updated<?", (now - self.retention,)
            ).rowcount
            live = {
                r[0]
                for r in conn.execute("SELECT object_key FROM objects WHERE object_key IS NOT NULL")
            }
            removed = 0
            for path in self.objects.iterdir():
                if (
                    path.name not in live
                    and re.fullmatch(r"[a-f0-9]{32}", path.name)
                    and not path.is_symlink()
                ):
                    path.unlink()
                    removed += 1
        return {"expired": expired, "orphan_files": removed}

    def archive_asset(self, asset, data):
        if (asset["kind"] == "model" and asset.get("format") != "GLB") or asset["kind"] not in {
            "image",
            "face_photo",
            "model",
            "pose",
            "tryon",
        }:
            return
        safe = {k: v for k, v in asset.items() if k not in {"filename", "owner_id", "url"}}
        safe["url"] = f"/api/account/storage/asset/{asset['id']}/file"
        self.put(asset["owner_id"], asset["id"], safe, data, category="asset", kind=asset["kind"])

    def archive_task(self, item, category="job"):
        if item.get("owner_id") and item["state"] in {
            "succeeded",
            "ready",
            "failed",
            "rejected",
            "cancelled",
        }:
            safe = {k: v for k, v in item.items() if k not in {"owner_id", "remote_id", "report"}}
            safe["steps"] = [
                {k: v for k, v in step.items() if k != "results"} for step in item.get("steps", [])
            ]
            self.put(item["owner_id"], item["id"], safe, category=category)


def private_router(vault, before_delete=None):
    router = APIRouter(prefix="/api/account/storage")
    User = Annotated[dict, Depends(current_user)]

    @router.get("")
    def manifest(user: User):
        return vault.manifest(user["id"])

    @router.post("/records/{item_id}")
    async def upload(
        item_id: str,
        user: User,
        value: Annotated[str, Form()],
        file: Annotated[UploadFile | None, File()] = None,
    ):
        try:
            if len(value.encode()) > 256 * 1024:
                raise ValueError("记录过大")
            document = json.loads(value)
            data = await file.read(MAX_MODEL + 1) if file else None
            kind = document.get("kind") if isinstance(document, dict) else None
            if data is not None and kind not in {"image", "face_photo", "model", "pose", "tryon"}:
                raise ValueError("资产类型无效")
            vault.put(user["id"], item_id, document, data, kind=kind)
            return {"synced": True}
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/{category}/{item_id}/file")
    def download(category: str, item_id: str, user: User):
        _, data, mime = vault.get(user["id"], item_id, category)
        if data is None:
            raise HTTPException(404, "记录没有文件")
        return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})

    @router.get("/{category}/{item_id}")
    def record(category: str, item_id: str, user: User):
        return vault.get(user["id"], item_id, category)[0]

    @router.delete("/items/{item_id}")
    def delete(item_id: str, user: User):
        vault.delete(user["id"], item_id)
        return {"deleted": True}

    @router.delete("")
    def delete_all(user: User):
        if before_delete:
            before_delete(user["id"])
        vault.delete(user["id"])
        return {"deleted": True}

    return router

import json
import sqlite3
import time
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4


class Store:
    def processing(self, scope, owner):
        return nullcontext()

    def finish(self, scope, keep=()):
        pass

    def checkpoint(self):
        pass

    def discard(self, asset_id):
        """Customers' temporary files only; durable stores keep everything."""
        pass

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "assets").mkdir(exist_ok=True)
        self.db = self.root / "studio.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, created REAL NOT NULL,
                    document TEXT NOT NULL
                );
            """)

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def new_asset_path(self, suffix: str) -> tuple[str, Path]:
        asset_id = uuid4().hex
        return asset_id, self.root / "assets" / f"{asset_id}.{suffix.lower()}"

    def add_asset(self, asset_id: str, path: Path, kind: str, **metadata) -> dict:
        asset = {
            "id": asset_id,
            "filename": path.name,
            "kind": kind,
            "size": path.stat().st_size,
            **metadata,
        }
        with self.connect() as conn:
            conn.execute("INSERT INTO assets VALUES (?, ?)", (asset_id, json.dumps(asset)))
        return asset

    def asset(self, asset_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT document FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def path(self, asset_id: str) -> Path:
        asset = self.asset(asset_id)
        if not asset:
            raise ValueError("Asset not found")
        return self.root / "assets" / asset["filename"]

    def create_job(self, request: dict, *, models: dict[str, str] | None = None,
                   job_id: str | None = None, owner_id: str | None = None) -> dict:
        job = {
            "id": job_id or uuid4().hex,
            "name": request["name"],
            "state": "queued",
            "created": time.time(),
            "updated": time.time(),
            "request": request,
            "models": models or {},
            "steps": [],
            "artifacts": [],
            "error": None,
            "pose_asset": None,
            "pose_approved": False,
        }
        if owner_id is not None:
            job["owner_id"] = owner_id
        self.save_job(job, new=True)
        return job

    def save_job(self, job: dict, *, new: bool = False):
        job["updated"] = time.time()
        with self.connect() as conn:
            if new:
                conn.execute(
                    "INSERT INTO jobs VALUES (?, ?, ?, ?)",
                    (job["id"], job["state"], job["created"], json.dumps(job)),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET state = ?, document = ? WHERE id = ?",
                    (job["state"], json.dumps(job), job["id"]),
                )

    def job(self, job_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT document FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def jobs(self, *, active: bool = False, owner_id: str | None = None) -> list[dict]:
        with self.connect() as conn:
            if active:
                rows = conn.execute(
                    "SELECT document FROM jobs WHERE state IN ('queued','running') ORDER BY created"
                ).fetchall()
            elif owner_id is not None:
                rows = conn.execute(
                    "SELECT document FROM jobs WHERE json_extract(document,'$.owner_id')=? "
                    "ORDER BY created DESC LIMIT 100", (owner_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT document FROM jobs ORDER BY created DESC LIMIT 100"
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def review(self, job_id: str, approve: bool) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT document FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            job = json.loads(row[0])
            if job["state"] != "awaiting_review":
                raise ValueError("任务当前不处于姿势图审核状态")
            job["pose_approved"] = approve
            job["state"] = "queued" if approve else "rejected"
            job["updated"] = time.time()
            conn.execute(
                "UPDATE jobs SET state = ?, document = ? WHERE id = ?",
                (job["state"], json.dumps(job), job_id),
            )
        return job

    def expired_reviews(self, deadline: float) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT document FROM jobs WHERE state='awaiting_review' AND created<?", (deadline,)).fetchall()
        return [json.loads(row[0]) for row in rows]


def public_asset(asset: dict) -> dict:
    return {k: v for k, v in asset.items() if k != "filename"} | {
        "url": f"/api/assets/{asset['id']}/file"
    }


def public_job(job: dict) -> dict:
    safe = {k: v for k, v in job.items() if k != "steps"}
    safe["steps"] = [
        {k: v for k, v in step.items() if k not in {"results"}} for step in job["steps"]
    ]
    return safe

"""Bounded, owner-scoped customer workspaces with no durable customer database."""

import copy
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from itp.storage import Store

TERMINAL = {"succeeded", "ready", "failed", "rejected", "cancelled"}


class MemoryDatabase:
    """Keep an SQLite database alive in RAM, retaining existing store interfaces."""

    def __init__(self):
        self.uri = f"file:itp-{uuid4().hex}?mode=memory&cache=shared"
        self.anchor = sqlite3.connect(self.uri, uri=True, timeout=10, check_same_thread=False)

    def connect(self):
        return sqlite3.connect(self.uri, uri=True, timeout=10)

    def close(self):
        self.anchor.close()


class TransientStore(Store):
    """Files exist only while processing; delivery bytes have a bounded RAM TTL.

    Browser acknowledgment deletes delivery bytes and task metadata. A janitor
    does the same if the browser disappears. Money is kept in the separate
    commerce database and is unaffected by acknowledgment or browser storage.
    """

    def __init__(self, root: Path, *, ttl=300, delivery_ttl=300, capacity=512 * 1024 * 1024):
        self.memory = MemoryDatabase()
        self.guard = threading.RLock()
        self.context = threading.local()
        self.ttl, self.delivery_ttl, self.capacity = ttl, delivery_ttl, capacity
        self.buffers: dict[str, bytes] = {}
        self.scopes: dict[str, dict] = {}
        self.touched: dict[str, float] = {}
        self.reserved: dict[str, Path] = {}
        temporary = root / "transient"
        temporary.mkdir(parents=True, exist_ok=True)
        temporary.chmod(0o700)
        super().__init__(Path(tempfile.mkdtemp(prefix="work-", dir=temporary)))
        self.root.chmod(0o700)
        (self.root / ".itp-workspace").write_text("ITP transient workspace v1", encoding="utf-8")

    def connect(self):
        return self.memory.connect()

    def save_job(self, job, *, new=False):
        with self.guard:
            current = self.job(job["id"])
            if current and current["state"] == "cancelled" and job["state"] != "cancelled":
                return
            super().save_job(job, new=new)

    def sweep_stale(self):
        # Only our marked temporary directories, under this exact work root.
        for path in self.root.parent.iterdir():
            marker = path / ".itp-workspace"
            if (
                path != self.root
                and path.name.startswith("work-")
                and not path.is_symlink()
                and marker.is_file()
                and marker.read_text() == "ITP transient workspace v1"
            ):
                shutil.rmtree(path)

    def new_asset_path(self, suffix):
        self.checkpoint()
        with self.guard:
            asset_id, path = super().new_asset_path(suffix)
            self.reserved[asset_id] = path
            scope = getattr(self.context, "scope", None)
            if scope in self.scopes:
                self.scopes[scope]["assets"].add(asset_id)
            return asset_id, path

    def add_asset(self, asset_id, path, kind, **metadata):
        self.checkpoint()
        owner = metadata.get("owner_id") or getattr(self.context, "owner", None)
        if not owner:
            raise ValueError("Customer assets require an owner")
        with self.guard:
            used = sum(a["size"] for a in self.all_assets())
            if used + path.stat().st_size > self.capacity:
                path.unlink(missing_ok=True)
                raise ValueError("临时工作区容量不足，请稍后重试")
            metadata["owner_id"] = owner
            asset = super().add_asset(asset_id, path, kind, **metadata)
            self.touched[asset_id] = time.time()
            self.reserved.pop(asset_id, None)
            return asset

    def all_assets(self):
        import json

        with self.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT document FROM assets")]

    def read(self, asset_id):
        with self.guard:
            if asset_id in self.buffers:
                return self.buffers[asset_id]
            return super().path(asset_id).read_bytes()

    def path(self, asset_id):
        self.checkpoint()
        with self.guard:
            path = super().path(asset_id)
            if not path.is_file() and asset_id in self.buffers:
                path.write_bytes(self.buffers[asset_id])
            return path

    def pin(self, scope, owner, asset_ids, *, created=None):
        with self.guard:
            for asset_id in asset_ids:
                asset = self.asset(asset_id)
                if not asset or asset.get("owner_id") != owner:
                    raise ValueError("输入资产不存在")
            self.scopes[scope] = {
                "owner": owner,
                "assets": set(asset_ids),
                "created": created or time.time(),
                "finished": None,
                "cancelled": False,
            }

    @contextmanager
    def processing(self, scope, owner):
        previous = (getattr(self.context, "scope", None), getattr(self.context, "owner", None))
        self.context.scope, self.context.owner = scope, owner
        try:
            yield
        finally:
            self.context.scope, self.context.owner = previous

    def checkpoint(self):
        scope = getattr(self.context, "scope", None)
        if scope and (scope not in self.scopes or self.scopes[scope]["cancelled"]):
            raise ValueError("任务已取消或临时数据已过期")

    def _delete(self, asset_id):
        asset = self.asset(asset_id)
        if asset:
            super().path(asset_id).unlink(missing_ok=True)
            with self.connect() as conn:
                conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        path = self.reserved.pop(asset_id, None)
        if path:
            path.unlink(missing_ok=True)
        self.buffers.pop(asset_id, None)
        self.touched.pop(asset_id, None)

    def discard(self, asset_id):
        with self.guard:
            self._delete(asset_id)

    def finish(self, scope, keep=()):
        with self.guard:
            item = self.scopes.get(scope)
            if not item:
                return
            keep = set(keep) if not item["cancelled"] else set()
            other = set().union(
                *(
                    s["assets"]
                    for key, s in self.scopes.items()
                    if key != scope and not s["finished"] and not s["cancelled"]
                )
            )
            for asset_id in item["assets"]:
                if asset_id in keep:
                    self.buffers[asset_id] = self.read(asset_id)
                    super().path(asset_id).unlink(missing_ok=True)
                elif asset_id not in other:
                    self._delete(asset_id)
            item["assets"] = keep
            item["finished"] = time.time()

    def cancel(self, scope):
        with self.guard:
            if scope in self.scopes:
                self.scopes[scope]["cancelled"] = True
                self.finish(scope)

    def acknowledge(self, scope, owner):
        with self.guard:
            item = self.scopes.get(scope)
            if not item or item["owner"] != owner:
                return
            if item["finished"] is None:
                raise ValueError("任务尚未完成")
            for asset_id in item["assets"]:
                if not any(
                    asset_id in s["assets"] and not s["finished"]
                    for key, s in self.scopes.items()
                    if key != scope
                ):
                    self._delete(asset_id)
            del self.scopes[scope]
            with self.connect() as conn:
                conn.execute("DELETE FROM jobs WHERE id=?", (scope,))

    def reap(self, timeout, *, now=None):
        """Return expired task IDs; the caller settles/refunds and drops metadata."""
        now = time.time() if now is None else now
        expired = []
        with self.guard:
            for scope, item in list(self.scopes.items()):
                start = item["finished"] or item["created"]
                limit = self.delivery_ttl if item["finished"] else timeout
                if now - start >= limit:
                    self.cancel(scope)
                    self.acknowledge(scope, item["owner"])
                    expired.append(scope)
            pinned = set().union(*(s["assets"] for s in self.scopes.values()))
            for asset_id, touched in list(self.touched.items()):
                if asset_id not in pinned and now - touched >= self.ttl:
                    self._delete(asset_id)
        return expired

    def close(self):
        with self.guard:
            self.buffers.clear()
            self.scopes.clear()
            self.memory.close()
            shutil.rmtree(self.root)


class TransientDocuments:
    """Owner-scoped task documents kept only in RAM, without photos/paths in SQL."""

    def __init__(self):
        self.guard = threading.RLock()
        self.items = {}

    def save(self, item):
        with self.guard:
            current = self.items.get(item["id"])
            if current and current.get("state") == "cancelled":
                return
            self.items[item["id"]] = copy.deepcopy(item)

    def get(self, item_id):
        with self.guard:
            return copy.deepcopy(self.items.get(item_id))

    def list(self, owner_id=None):
        with self.guard:
            return sorted(
                (
                    copy.deepcopy(i)
                    for i in self.items.values()
                    if owner_id is None or i.get("owner_id") == owner_id
                ),
                key=lambda i: i["created"],
                reverse=True,
            )

    def for_mesh(self, mesh_asset, owner_id=None):
        return [
            i for i in self.list(owner_id)
            if i.get("mesh_asset") == mesh_asset
        ]

    def active(self):
        return [i for i in self.list() if i["state"] in {"queued", "running", "submitting"}]

    def delete(self, item_id):
        with self.guard:
            self.items.pop(item_id, None)

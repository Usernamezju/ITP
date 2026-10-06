"""Customer files are temporary, while delivery is bounded RAM only."""

import time

import pytest

from itp.transient import TransientDocuments, TransientStore


def asset(store, owner="alice", raw=b"image"):
    asset_id, path = store.new_asset_path("png")
    path.write_bytes(raw)
    return store.add_asset(asset_id, path, "image", owner_id=owner)


def test_no_customer_database_on_disk_and_cleanup(tmp_path):
    store = TransientStore(tmp_path)
    original = asset(store)
    store.pin("task", "alice", [original["id"]])
    with store.processing("task", "alice"):
        output = asset(store, raw=b"output")
        # Explicit owner also works in the API thread.
        store.scopes["task"]["assets"].add(output["id"])
    store.finish("task", [output["id"]])
    assert not list(tmp_path.rglob("*.sqlite3"))
    assert not list((store.root / "assets").iterdir())
    assert store.asset(original["id"]) is None
    assert store.read(output["id"]) == b"output"
    store.acknowledge("task", "bob")
    assert store.read(output["id"]) == b"output"
    store.acknowledge("task", "alice")
    assert not store.buffers and not store.all_assets()
    directory = store.root
    store.close()
    assert not directory.exists()


def test_unowned_and_cross_user_inputs_rejected(tmp_path):
    store = TransientStore(tmp_path)
    image = asset(store)
    with pytest.raises(ValueError):
        store.pin("bob-task", "bob", [image["id"]])
    ident, path = store.new_asset_path("png")
    path.write_bytes(b"test")
    with pytest.raises(ValueError):
        store.add_asset(ident, path, "image")
    store.close()


def test_reap_staged_active_and_delivery_data(tmp_path):
    store = TransientStore(tmp_path, ttl=2, delivery_ttl=2)
    staged, active, delivery = [asset(store) for _ in range(3)]
    now = time.time()
    store.pin("active", "alice", [active["id"]], created=now - 20)
    store.pin("delivery", "alice", [delivery["id"]])
    store.finish("delivery", [delivery["id"]])
    assert set(store.reap(10, now=now + 3)) == {"active", "delivery"}
    assert not store.all_assets() and not store.buffers
    assert not list((store.root / "assets").iterdir())
    store.close()


def test_cancelled_worker_cannot_recreate_private_files(tmp_path):
    store = TransientStore(tmp_path)
    original = asset(store)
    store.pin("task", "alice", [original["id"]])
    with store.processing("task", "alice"):
        store.cancel("task")
        with pytest.raises(ValueError):
            store.new_asset_path("glb")
        with pytest.raises(ValueError):
            store.path(original["id"])
    assert not store.all_assets()
    store.close()


def test_task_documents_are_owner_scoped_and_copy_isolated():
    documents = TransientDocuments()
    documents.save({"id": "a", "owner_id": "alice", "created": 1, "state": "queued"})
    documents.save({"id": "b", "owner_id": "bob", "created": 2, "state": "queued"})
    assert [i["id"] for i in documents.list("alice")] == ["a"]
    item = documents.get("a")
    item["state"] = "ready"
    assert documents.get("a")["state"] == "queued"
    item["state"] = "cancelled"
    documents.save(item)
    item["state"] = "ready"
    documents.save(item)
    assert documents.get("a")["state"] == "cancelled"

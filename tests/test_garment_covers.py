"""Cover selection for product cards: what a card is told, and what it can show.

The failure this guards against is a product losing its picture because one
file did: a gallery row can outlive its file whenever a data directory is
restored without the asset store, and a card that trusts "there is a row"
renders a broken frame instead of the shop's next picture.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from itp.api import create_app
from itp.config import Settings

PASSWORD = "secret-password"


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        segmentation_model=tmp_path / "missing.onnx",
        poll_seconds=0.05,
    )
    app = create_app(settings, start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client, settings


def png_bytes(color="orange"):
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buffer, format="PNG")
    return buffer.getvalue()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def token_for(client, name="shop-one"):
    client.post("/api/merchant/register", json={
        "name": name, "display_name": f"{name} 店铺",
        "contact": "owner@example.com", "password": PASSWORD,
    })
    login = client.post("/api/merchant/login", json={"name": name, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def create_garment(client, token, images=(), **overrides):
    document = {"category": "上装", "name": "落肩针织开衫", "status": "published"}
    document.update(overrides)
    files = [("images", (f"image-{index}.png", data, "image/png"))
             for index, data in enumerate(images)]
    return client.post(
        "/api/merchant/garments",
        headers=auth(token),
        data={"payload": json.dumps(document, ensure_ascii=False)},
        files=files or None,
    )


def stored_file(settings, client, url):
    """Follow one image URL's asset row to the file actually on disk."""
    image_id = url.rsplit("/", 1)[-1]
    store = client.app.state.commercial_assets
    image = client.app.state.merchants.image(image_id)
    asset = store.asset(image["asset_id"])
    return store.root / "assets" / asset["filename"]


# --------------------------------------------------------------- empty covers


def test_a_product_with_no_picture_still_lists(env):
    client, _ = env
    token = token_for(client)
    garment = create_garment(client, token).json()
    assert garment["images"] == []

    listed = client.get("/api/garments").json()
    assert [item["id"] for item in listed["items"]] == [garment["id"]]
    assert listed["items"][0]["images"] == []

    detail = client.get(f"/api/garments/{garment['id']}")
    assert detail.status_code == 200 and detail.json()["images"] == []


# -------------------------------------------------------------- lost pictures


def test_a_picture_whose_file_is_gone_is_marked_not_dropped(env):
    client, settings = env
    token = token_for(client)
    garment = create_garment(client, token, images=[png_bytes("orange"), png_bytes("blue")]).json()
    first, second = garment["images"]
    assert [image["available"] for image in garment["images"]] == [True, True]

    stored_file(settings, client, first["url"]).unlink()

    listed = client.get("/api/merchant/garments", headers=auth(token)).json()["items"][0]
    # The row is still listed — only the shop can replace it — but it is flagged
    # so every card steps over it instead of showing a broken frame.
    assert [image["id"] for image in listed["images"]] == [first["id"], second["id"]]
    assert [image["available"] for image in listed["images"]] == [False, True]
    assert all(image["available"] for image in
               client.get(f"/api/garments/{garment['id']}").json()["images"][1:])


def test_the_image_route_refuses_a_lost_file(env):
    client, settings = env
    token = token_for(client)
    garment = create_garment(client, token, images=[png_bytes()]).json()
    image = garment["images"][0]
    assert client.get(image["url"]).status_code == 200

    stored_file(settings, client, image["url"]).unlink()
    assert client.get(image["url"]).status_code == 404

    # An id that never existed, and a row whose asset row is gone, are refused
    # the same way: the route answers for the file, not for the row.
    assert client.get("/api/garment-images/" + "f" * 32).status_code == 404
    client.app.state.merchants.delete_image(
        client.app.state.merchants.garment(garment["id"])["merchant_id"],
        garment["id"], image["id"],
    )
    assert client.get(image["url"]).status_code == 404


def test_the_cover_is_the_first_picture_that_still_loads(env):
    client, settings = env
    token = token_for(client)
    garment = create_garment(client, token, images=[png_bytes("orange"), png_bytes("blue")]).json()
    stored_file(settings, client, garment["images"][0]["url"]).unlink()

    # The public list is what a recommendation card reads: the first entry has
    # to be a picture the browser can actually fetch.
    listed = client.get("/api/garments").json()["items"][0]
    available = [image for image in listed["images"] if image["available"]]
    assert available and available[0]["id"] == garment["images"][1]["id"]
    assert client.get(available[0]["url"]).status_code == 200


def test_a_picture_added_after_the_database_outlived_its_files(env):
    """The shape a restored data directory leaves behind: rows, no files."""
    client, settings = env
    token = token_for(client)
    garment = create_garment(client, token, images=[png_bytes(), png_bytes()]).json()

    for path in (settings.data_dir / "commercial" / "assets").glob("*.png"):
        path.unlink()

    listed = client.get("/api/merchant/garments", headers=auth(token)).json()["items"][0]
    assert [image["available"] for image in listed["images"]] == [False, False]
    assert all(client.get(image["url"]).status_code == 404 for image in listed["images"])

    # Re-uploading repairs the product without touching the stale rows.
    added = client.post(
        f"/api/merchant/garments/{garment['id']}/images",
        headers=auth(token), files=[("images", ("new.png", png_bytes("green"), "image/png"))],
    )
    assert added.status_code == 201, added.text
    # The route answers with the whole gallery, oldest first: the new picture is
    # appended, and it is the one the card will now reach.
    repaired = added.json()[-1]
    assert repaired["available"] is True
    assert client.get(repaired["url"]).status_code == 200


# ------------------------------------------------------------- second client


def test_the_shop_and_its_customers_agree_on_the_cover(env):
    """Two callers, two routes, one answer — the decision is the server's."""
    client, settings = env
    token = token_for(client)
    garment = create_garment(client, token, images=[png_bytes("orange"), png_bytes("blue")]).json()
    stored_file(settings, client, garment["images"][0]["url"]).unlink()

    shop = client.get(f"/api/merchant/garments/{garment['id']}", headers=auth(token)).json()
    public = client.get(f"/api/garments/{garment['id']}").json()
    assert public["images"] == shop["images"]
    assert [image["available"] for image in public["images"]] == [False, True]

    # A device with no session at all is told the same thing, so the cover a
    # card shows never depends on which browser is asking.
    anonymous = TestClient(client.app, base_url="http://localhost:8000").get(
        f"/api/garments/{garment['id']}")
    assert [image["available"] for image in anonymous.json()["images"]] == [False, True]

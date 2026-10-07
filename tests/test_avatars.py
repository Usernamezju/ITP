"""Account avatars: re-encoded on upload, served under an unguessable key."""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from itp.api import create_app
from itp.avatars import AVATAR_SIZE, AvatarStore, render_avatar
from itp.config import Settings

SECRET = "e" * 64
PASSWORD = "avatar-password-123"


def picture(size=(512, 512), frames=1) -> bytes:
    stream = io.BytesIO()
    if frames > 1:  # An animated PNG: the format is allowed, the animation is not.
        single = [Image.new("RGB", size, color) for color in ("red", "blue")[:frames]]
        single[0].save(stream, format="PNG", save_all=True, append_images=single[1:])
    else:
        Image.new("RGB", size, "orange").save(stream, format="PNG")
    return stream.getvalue()


def test_render_avatar_squares_reencodes_and_drops_metadata():
    source = io.BytesIO()
    image = Image.new("RGB", (640, 480), "orange")
    exif = image.getexif()
    exif[34853] = {1: "N"}  # GPS IFD: a camera would leave this behind
    image.save(source, format="JPEG", exif=exif)
    original = source.getvalue()
    assert Image.open(io.BytesIO(original)).getexif()  # the upload really carries EXIF

    rendered = render_avatar(original)
    with Image.open(io.BytesIO(rendered)) as result:
        assert result.format == "PNG"
        assert result.size == (AVATAR_SIZE, AVATAR_SIZE)
        assert not result.getexif()


@pytest.mark.parametrize("data,message", [
    (b"", "请选择头像图片"),
    (b"x" * (4 * 1024 * 1024 + 1), "头像不能超过 4 MiB"),
    (b"not an image at all", "头像文件损坏或格式不支持"),
])
def test_render_avatar_rejects_unusable_uploads(data, message):
    with pytest.raises(ValueError) as error:
        render_avatar(data)
    assert message in str(error.value)


def test_render_avatar_rejects_tiny_and_animated_pictures():
    with pytest.raises(ValueError, match="短边至少"):
        render_avatar(picture((32, 32)))
    with pytest.raises(ValueError, match="动态图片"):
        render_avatar(picture((256, 256), frames=2))


def test_avatar_store_round_trip_and_key_validation(tmp_path):
    store = AvatarStore(tmp_path / "avatars")
    key = store.save(render_avatar(picture()))
    path = store.path(key)
    assert path and path.is_file() and path.name == f"{key}.png"
    for bad in ("../../etc/passwd", "not-a-key", "", key.upper(), key + "/x"):
        assert store.path(bad) is None
    store.remove(key)
    assert store.path(key) is None


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET,
                 environment="test"),
        start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        client.post("/api/auth/register", json={"name": "avatar-user", "display_name": "头像用户",
                                                "password": PASSWORD, "role": "customer"})
        login = client.post("/api/auth/login", json={"name": "avatar-user", "password": PASSWORD})
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        yield client


def upload(client, data: bytes, name="avatar.png"):
    return client.post("/api/account/avatar", files={"file": (name, data, "image/png")})


def test_upload_serves_the_picture_and_requires_a_session(client, tmp_path):
    anonymous = TestClient(client.app, base_url="http://localhost:8000")
    assert upload(anonymous, picture()).status_code == 401
    assert anonymous.delete("/api/account/avatar").status_code == 401
    assert client.get("/api/account/me").json()["avatar_key"] is None

    response = upload(client, picture())
    assert response.status_code == 200, response.text
    key = response.json()["avatar_key"]
    assert key and len(key) == 32
    assert client.get("/api/account/me").json()["avatar_key"] == key

    image = client.get(f"/api/avatars/{key}")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert "immutable" in image.headers["cache-control"]
    with Image.open(io.BytesIO(image.content)) as stored:
        assert stored.size == (AVATAR_SIZE, AVATAR_SIZE)
    assert (tmp_path / "data" / "avatars" / f"{key}.png").is_file()
    # The picture is public under its random key, but nothing else is exposed.
    # The client and proxies normalise plain "../" segments, so the handler must
    # refuse an encoded traversal that actually reaches it.
    assert client.get("/api/avatars/%2e%2e%2f%2e%2e%2fetc%2fpasswd").status_code == 404
    assert client.get("/api/avatars/" + "f" * 32).status_code == 404


def test_replacing_and_clearing_an_avatar_retires_the_old_url(client, tmp_path):
    first = upload(client, picture()).json()["avatar_key"]
    second = upload(client, picture((300, 300))).json()["avatar_key"]
    assert second != first
    assert client.get(f"/api/avatars/{first}").status_code == 404
    assert not (tmp_path / "data" / "avatars" / f"{first}.png").exists()
    assert client.get(f"/api/avatars/{second}").status_code == 200

    cleared = client.delete("/api/account/avatar")
    assert cleared.status_code == 200 and cleared.json()["avatar_key"] is None
    assert client.get(f"/api/avatars/{second}").status_code == 404
    assert client.get("/api/account/me").json()["avatar_key"] is None


def test_unusable_uploads_are_refused_and_keep_the_current_avatar(client, tmp_path):
    key = upload(client, picture()).json()["avatar_key"]
    assert upload(client, b"not an image").status_code == 422
    assert upload(client, picture((16, 16))).status_code == 422
    assert upload(client, b"x" * (4 * 1024 * 1024 + 1)).status_code == 422
    assert client.get("/api/account/me").json()["avatar_key"] == key
    assert client.get(f"/api/avatars/{key}").status_code == 200


def test_the_merchant_profile_carries_the_same_avatar(client):
    key = upload(client, picture()).json()["avatar_key"]
    merchant = client.app.state.merchants
    assert merchant.merchant_by_name("avatar-user")["avatar_key"] == key
    assert merchant.list_accounts()[1][0]["avatar_key"] == key
    # A shop registered alongside starts on the default and can upload its own.
    client.post("/api/auth/register", json={"name": "avatar-shop", "display_name": "头像店铺",
                                            "password": PASSWORD, "role": "merchant"})
    login = client.post("/api/auth/login", json={"name": "avatar-shop", "password": PASSWORD})
    shop = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.get("/api/merchant/me", headers=shop).json()["avatar_key"] is None
    uploaded = client.post("/api/account/avatar", headers=shop,
                           files={"file": ("shop.png", picture((320, 320)), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["avatar_key"]
    assert client.get("/api/merchant/me", headers=shop).json()["avatar_key"] == (
        uploaded.json()["avatar_key"])


def test_the_uploads_land_in_their_own_directory(client, tmp_path):
    upload(client, picture())
    files = sorted(Path(tmp_path / "data" / "avatars").iterdir())
    assert len(files) == 1 and files[0].suffix == ".png"

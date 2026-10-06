"""The single-page app is served for every customer, merchant and admin URL."""

import pytest
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings


@pytest.fixture
def dist(tmp_path):
    """A stand-in build directory, so the tests do not need a real bundle."""
    directory = tmp_path / "dist"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text("<!doctype html><title>ITP STUDIO</title>")
    (directory / "assets" / "app.js").write_text("console.log('itp')")
    return directory


def app_for(tmp_path, frontend_dir):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    return create_app(settings, start_worker=False, frontend_dir=frontend_dir)


@pytest.mark.parametrize("path", [
    "/", "/tryon", "/outfits", "/history", "/account", "/appearance", "/merchant", "/admin",
])
def test_every_page_url_returns_the_app(tmp_path, dist, path):
    with TestClient(app_for(tmp_path, dist), base_url="http://localhost:8000") as client:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "ITP STUDIO" in response.text


def test_built_files_win_and_api_paths_never_fall_back(tmp_path, dist):
    with TestClient(app_for(tmp_path, dist), base_url="http://localhost:8000") as client:
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert asset.text == "console.log('itp')"
        assert client.get("/api").status_code == 404
        assert client.get("/api/nope").status_code == 404
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        # FastAPI's own documentation stays reachable, not swallowed by the app.
        assert "swagger" in client.get("/docs").text.lower()


def test_a_deep_link_never_escapes_the_build_directory(tmp_path, dist):
    outside = tmp_path / "secret.txt"
    outside.write_text("do not serve")
    with TestClient(app_for(tmp_path, dist), base_url="http://localhost:8000") as client:
        for path in ("/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt",
                     "/..%2f..%2fsecret.txt"):
            response = client.get(path)
            assert "do not serve" not in response.text
            assert response.status_code in (200, 404)


def test_without_a_build_only_the_api_answers(tmp_path):
    missing = tmp_path / "no-dist"
    with TestClient(app_for(tmp_path, missing), base_url="http://localhost:8000") as client:
        root = client.get("/")
        assert root.status_code == 200
        assert root.json()["docs"] == "/docs"
        assert client.get("/tryon").status_code == 404
        assert client.get("/api/health").status_code == 200

"""Regression guards for public proxy access boundaries."""

import re
from pathlib import Path


def test_public_settings_are_not_exposed_to_site_users():
    config = (Path(__file__).parents[1] / "deploy/autodl/nginx-itp.conf").read_text()
    assert "auth_basic" not in config
    assert "htpasswd" not in config
    settings = config.split("location = /api/settings {", 1)[1]
    settings = settings.split("\n    }", 1)[0]
    assert "auth_basic off" not in settings
    assert "return 404;" in settings
    assert "proxy_pass" not in settings


def test_merchant_upload_limits_remain_without_site_auth():
    config = (Path(__file__).parents[1] / "deploy/autodl/nginx-itp.conf").read_text()
    guard = r"^/api/merchant/(me|password|garments|looks|analytics)(/|$)"
    route = config.split(f"location ~ {guard} {{", 1)[1].split("\n    }", 1)[0]
    assert "auth_basic" not in route
    assert "client_max_body_size 81m;" in route
    assert "proxy_pass http://127.0.0.1:8000;" in route
    pattern = re.compile(guard)
    for path in ("/api/merchant/me", "/api/merchant/password", "/api/merchant/garments",
                 "/api/merchant/garments/" + "a" * 32 + "/images", "/api/merchant/looks"):
        assert pattern.search(path)
    for path in ("/api/settings", "/api/merchant/login", "/api/merchant/register",
                 "/api/merchant/me-spoof", "/", "/api/body-profile"):
        assert not pattern.search(path)


def test_admin_proxy_keeps_application_auth_boundary():
    config = (Path(__file__).parents[1] / "deploy/autodl/nginx-itp.conf").read_text()
    guard = r"^/api/admin(/|$)"
    route = config.split(f"location ~ {guard} {{", 1)[1].split("\n    }", 1)[0]
    assert "auth_basic" not in route
    assert "proxy_pass http://127.0.0.1:8000;" in route
    pattern = re.compile(guard)
    for path in ("/api/admin/status", "/api/admin/settings", "/api/admin/accounts",
                 "/api/admin/usage", "/api/admin/orders", "/api/admin/jobs"):
        assert pattern.search(path)
    for path in ("/api/admin-backdoor", "/api/administrator", "/admin", "/api/settings",
                 "/api/merchant/me"):
        assert not pattern.search(path)


def test_public_deployment_blocks_legacy_shared_measurements():
    config = (Path(__file__).parents[1] / "deploy/autodl/nginx-itp.conf").read_text()
    route = config.split("location = /api/body-profile {", 1)[1].split("}", 1)[0]
    assert "return 404;" in route
    script = (Path(__file__).parents[1] / "deploy/autodl/configure.py").read_text()
    assert "htpasswd" not in script
    assert "access-password" not in script


def test_public_app_pages_open_but_accounts_and_shared_data_are_protected(tmp_path):
    from fastapi.testclient import TestClient
    from itp.api import create_app
    from itp.config import Settings

    app = create_app(Settings(_env_file=None, data_dir=tmp_path,
                             public_origin="https://shop.example.com"),
                     start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        for path in ("/", "/merchant"):
            response = client.get(path)
            assert response.status_code == 200
            assert "www-authenticate" not in response.headers
        for path in ("/api/account/commerce", "/api/account/ledger", "/api/admin/status"):
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers.get("www-authenticate") == "Bearer"
        assert client.get("/api/body-profile").status_code == 404
        assert client.put("/api/body-profile", json={"height_cm": 170}).status_code == 404

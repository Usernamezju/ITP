"""Regression guards for public proxy access boundaries."""

import re
from pathlib import Path


def test_public_settings_are_not_exposed_to_site_users():
    config = (Path(__file__).parents[1] / "deploy/autodl/nginx-itp.conf").read_text()
    assert 'auth_basic "ITP Studio";' in config
    assert "auth_basic_user_file /etc/nginx/itp.htpasswd;" in config
    settings = config.split("location = /api/settings {", 1)[1]
    settings = settings.split("\n    }", 1)[0]
    assert "auth_basic off" not in settings
    assert "return 404;" in settings
    assert "proxy_pass" not in settings


def test_only_jwt_guarded_merchant_routes_bypass_basic_auth():
    config = (Path(__file__).parents[1] / "deploy/autodl/nginx-itp.conf").read_text()
    guard = r"^/api/merchant/(me|password|garments|looks)(/|$)"
    route = config.split(f"location ~ {guard} {{", 1)[1].split("\n    }", 1)[0]
    assert config.count("auth_basic off;") == 1
    assert "auth_basic off;" in route
    assert "client_max_body_size 81m;" in route
    assert "proxy_pass http://127.0.0.1:8000;" in route
    pattern = re.compile(guard)
    for path in ("/api/merchant/me", "/api/merchant/password", "/api/merchant/garments",
                 "/api/merchant/garments/" + "a" * 32 + "/images", "/api/merchant/looks"):
        assert pattern.search(path)
    for path in ("/api/settings", "/api/merchant/login", "/api/merchant/register",
                 "/api/merchant/me-spoof", "/", "/api/body-profile"):
        assert not pattern.search(path)

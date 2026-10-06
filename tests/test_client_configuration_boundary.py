"""Customer clients never own or modify platform provider credentials."""

import pytest
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings


@pytest.mark.parametrize("method", ["GET", "PATCH", "POST", "PUT", "DELETE", "HEAD"])
@pytest.mark.parametrize("suffix", ["", "/"])
def test_public_settings_are_unavailable_and_leave_env_unchanged(tmp_path, method, suffix):
    path = tmp_path / ".env"
    original = "# operator configuration\nITP_FLUX_API_KEY=existing-secret\n"
    path.write_text(original)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data",
                              public_origin="https://example.org:8443",
                              flux_api_key="existing-secret"),
                     config_path=path, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.request(method, "/api/settings" + suffix,
                                  json={"flux_api_key": "replacement"})
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert "existing-secret" not in response.text
        assert "replacement" not in response.text
        assert path.read_text() == original
        assert app.state.settings.flux_api_key.get_secret_value() == "existing-secret"
        assert "/api/settings" not in client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize("headers", [
    {"Origin": "http://localhost:8000"},
    {"X-Forwarded-For": "198.51.100.9"},
    {"X-Forwarded-Proto": "https"},
])
def test_browser_and_proxy_cannot_use_legacy_local_operator_api(tmp_path, headers):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), start_worker=False,
                     config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.get("/api/settings", headers=headers).status_code == 404
        assert client.patch("/api/settings", headers=headers,
                            json={"flux_api_key": "private"}).status_code == 404
        assert not (tmp_path / ".env").exists()


def test_capabilities_remain_usable_without_exposing_provider_configuration(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path,
                              public_origin="https://example.org:8443",
                              flux_endpoint="https://api.bfl.ai/v1/flux-2-pro",
                              flux_api_key="platform-secret"), start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.get("/api/capabilities")
        assert response.status_code == 200
        assert response.json()["tryon_providers"]["flux"] is True
        assert "platform-secret" not in response.text
        assert "api.bfl.ai" not in response.text

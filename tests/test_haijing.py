"""Verify the supplied relay trial protocol without external or charged requests."""

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from itp.api import create_app
from auth_helpers import fund_client
from itp.config import HAIJING_GENERATION_ENDPOINT, Settings
from itp.preprocessing import MAX_UPLOAD, image_base64
from itp.tryon import FluxProvider


@pytest.mark.parametrize("provider", ["flux", "flux_max"])
def test_haijing_settings_save_and_create_multi_reference_tryon(
    settings, tmp_path, image_bytes, provider,
):
    config = tmp_path / ".env"
    app = create_app(settings, start_worker=False, config_path=config)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        response = client.patch("/api/settings", json={
            f"{provider}_endpoint": HAIJING_GENERATION_ENDPOINT,
            f"{provider}_api_key": "test-relay-key",
        })
        assert response.status_code == 200
        assert "test-relay-key" not in response.text
        assert response.json()[f"{provider}_api_key_set"] is True
        restored = Settings(_env_file=config)
        assert getattr(restored, f"{provider}_endpoint") == HAIJING_GENERATION_ENDPOINT
        caps = client.get("/api/capabilities").json()
        assert caps["tryon_providers"][provider] is True
        asset = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        task = client.post("/api/tryons", json={
            "provider": provider, "person": {"front": asset["id"]},
            "garment": {"front": asset["id"]},
        })
        assert task.status_code == 201
        assert task.json()["provider"] == provider
        assert task.json()["model"] == settings.tryon_model_for(provider)
        assert task.json()["request"]["person"] == {"front": asset["id"]}
        assert len(client.get("/api/tryons").json()) == 1
        assert client.post("/api/jobs", json={"front": asset["id"]}).status_code == 201


@pytest.mark.parametrize("provider", ["flux", "flux_max"])
@pytest.mark.parametrize("result_format", ["url", "b64_json"])
def test_haijing_numbered_references_and_image_result(
    settings, store, image_bytes, provider, result_format, tmp_path,
):
    settings = settings.model_copy(update={
        f"{provider}_endpoint": HAIJING_GENERATION_ENDPOINT,
        f"{provider}_api_key": SecretStr("test-relay-key"),
    })
    from PIL import Image

    paths = []
    for index, color in enumerate(("red", "blue", "green", "orange", "purple")):
        path = tmp_path / f"reference-{index}.png"
        Image.new("RGB", (256, 256), color).save(path)
        paths.append(path)
    model = settings.tryon_model_for(provider)
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            assert str(request.url) == HAIJING_GENERATION_ENDPOINT
            assert request.headers["Authorization"] == "Bearer test-relay-key"
            assert "x-key" not in request.headers
            payload = json.loads(request.content)
            expected = {"model": model, "prompt": "Keep identity", "aspect_ratio": "3:4"}
            expected.update({"input_image" if i == 0 else f"input_image_{i + 1}": image_base64(path)
                             for i, path in enumerate(paths)})
            assert payload == expected
            result = ({"url": "https://images.example/result.png"} if result_format == "url"
                      else {"b64_json": base64.b64encode(image_bytes).decode()})
            return httpx.Response(200, json={"data": [result]})
        assert str(request.url) == "https://images.example/result.png"
        assert "authorization" not in request.headers
        assert "x-key" not in request.headers
        return httpx.Response(200, content=image_bytes)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = FluxProvider(settings, client, provider=provider).generate(
            paths, "Keep identity", model,
        )
        assert result == image_bytes
    assert len(requests) == (2 if result_format == "url" else 1)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_relay_errors_show_status_but_not_secrets(settings, store, status):
    settings = settings.model_copy(update={
        "flux_endpoint": HAIJING_GENERATION_ENDPOINT, "flux_api_key": SecretStr("private-key"),
    })
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": "private-key echoed by server"}),
    )) as client:
        with pytest.raises(RuntimeError, match=f"HTTP {status}") as caught:
            FluxProvider(settings, client).generate(
                [store.path(store.test_image)], "Try on", "flux-2-pro",
            )
    assert "private-key" not in str(caught.value)


@pytest.mark.parametrize("result,match", [
    ({"url": "http://images.example/a.png"}, "地址不可信"),
    ({"url": "https://127.0.0.1/a.png"}, "地址不可信"),
    ({"url": "https://localhost./a.png"}, "地址不可信"),
    ({"url": "https://user:secret@images.example/a.png"}, "地址不可信"),
    ({"b64_json": "not-base64!"}, "响应格式无效"),
    ({"b64_json": "A" * (4 * ((MAX_UPLOAD + 2) // 3) + 4)}, "超过 10 MiB"),
    ({}, "未返回"),
])
def test_relay_rejects_unsafe_or_invalid_results(settings, store, result, match):
    settings = settings.model_copy(update={
        "flux_endpoint": HAIJING_GENERATION_ENDPOINT, "flux_api_key": SecretStr("test-key"),
    })
    def handle(request):
        assert request.method == "POST"
        return httpx.Response(200, json={"data": [result]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises((RuntimeError, ValueError), match=match):
            FluxProvider(settings, client).generate(
                [store.path(store.test_image)], "Try on", "flux-2-pro",
            )


@pytest.mark.parametrize("status,headers,content", [
    (302, {"location": "https://127.0.0.1/private"}, b""),
    (200, {"content-length": str(MAX_UPLOAD + 1)}, b""),
    (200, {}, b"a" * (MAX_UPLOAD + 1)),
    (200, {}, b""),
])
def test_relay_download_is_bounded_and_never_redirects(settings, store, status, headers, content):
    settings = settings.model_copy(update={
        "flux_endpoint": HAIJING_GENERATION_ENDPOINT, "flux_api_key": SecretStr("test-key"),
    })
    def handle(request):
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://images.example/a.png"}]})
        assert str(request.url) == "https://images.example/a.png"
        return httpx.Response(status, headers=headers, content=content)
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        with pytest.raises((RuntimeError, ValueError)):
            FluxProvider(settings, client).generate(
                [store.path(store.test_image)], "Try on", "flux-2-pro",
            )


@pytest.mark.parametrize("endpoint", [
    "http://api.haijingai.com/v2/images/generations",
    "https://api.haijingai.com/v2/images/generations?api_key=secret",
    "https://api.haijingai.com.evil.example/v2/images/generations",
])
def test_only_exact_documented_relay_address_is_accepted(endpoint):
    with pytest.raises(ValueError):
        Settings(_env_file=None, flux_max_endpoint=endpoint)

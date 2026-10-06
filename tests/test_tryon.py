import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from itp.api import create_app
from auth_helpers import fund_client
from itp.tryon import VIEWS, SeedDreamProvider, FluxProvider, FluxKleinProvider, GPTImageProvider, TryOnRequest, TryOnStore, TryOnWorker, closest_view


def test_tryon_requires_configuration_without_affecting_3d(settings, image_bytes):
    settings = settings.model_copy(update={"seedream_endpoint": ""})
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        asset = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        assert client.get("/api/capabilities").json()["tryon"] is False
        assert client.post("/api/jobs", json={"front": asset["id"]}).status_code == 201
        result = client.post("/api/tryons", json={
            "person": {view: asset["id"] for view in VIEWS},
            "garment": {view: asset["id"] for view in VIEWS},
            "consistent_confirmed": True,
        })
        assert result.status_code == 503


@pytest.mark.parametrize("variant", ["seedream", "flux_max"])
def test_tryon_six_results_continue_without_upload(settings, image_bytes, variant):
    settings = settings.model_copy(update={
        "seedream_endpoint": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        "seedream_api_key": SecretStr("test-only"),
        "flux_max_endpoint": "https://api.bfl.ai/v1/flux-2-max",
        "flux_max_api_key": SecretStr("max-test-only"),
    })
    app = create_app(settings, start_worker=False)
    calls = []

    def fake_generate(paths, prompt, model):
        calls.append((len(paths), prompt, model))
        return image_bytes

    app.state.tryon_worker.providers[variant].generate = fake_generate
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        asset = client.post("/api/assets", files={"file": ("source.png", image_bytes)}).json()
        payload = {"name": "测试试穿", "provider": variant,
                   "person": {view: asset["id"] for view in VIEWS},
                   "garment": {view: asset["id"] for view in VIEWS}}
        created = client.post("/api/tryons", json=payload)
        assert created.status_code == 201, created.text
        tryon = created.json()
        assert client.post(f"/api/tryons/{tryon['id']}/continue").status_code == 409
        app.state.tryon_worker.run_job(tryon)
        ready = client.get(f"/api/tryons/{tryon['id']}").json()
        assert ready["state"] == "ready"
        assert set(ready["results"]) == set(VIEWS)
        assert [call[0] for call in calls] == [2, 5, 5, 5, 5, 5]
        assert all(call[2] == settings.tryon_model_for(variant) for call in calls)
        for asset_id in ready["results"].values():
            assert client.get(f"/api/assets/{asset_id}/file").status_code == 200
        continued = client.post(f"/api/tryons/{tryon['id']}/continue")
        assert continued.status_code == 201, continued.text
        request = continued.json()["request"]
        assert request["front"] == ready["results"]["front"]
        assert set(request["views"]) == set(VIEWS) - {"front"}
        assert request["views_consistent_confirmed"] is True


def test_tryon_provider_surfaces_ark_error_code(settings, store):
    settings = settings.model_copy(update={
        "seedream_endpoint": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        "seedream_api_key": SecretStr("test-only"),
    })

    class StubClient:
        def post(self, url, json, headers):
            return httpx.Response(
                403, json={"error": {"code": "ModelNotOpen", "message": "该模型未开通"}}
            )

    provider = SeedDreamProvider(settings, client=StubClient())
    with pytest.raises(RuntimeError) as excinfo:
        provider.generate([store.path(store.test_image)], "prompt", settings.seedream_model)
    message = str(excinfo.value)
    assert "HTTP 403" in message
    assert "ModelNotOpen" in message
    assert "该模型未开通" in message
    assert "模型尚未开通；请在方舟控制台开通该模型" in message


def test_tryon_accepts_partial_views_and_rejects_invalid_views(settings, image_bytes):
    settings = settings.model_copy(update={
        "seedream_endpoint": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        "seedream_api_key": SecretStr("test-only"),
    })
    app = create_app(settings, start_worker=False)
    with fund_client(TestClient(app, base_url="http://localhost:8000")) as client:
        asset = client.post("/api/assets", files={"file": ("a.png", image_bytes)}).json()
        payload = {"person": {"front": asset["id"]}, "garment": {"back": asset["id"]}}
        created = client.post("/api/tryons", json=payload)
        assert created.status_code == 201, created.text
        job = created.json()
        app.state.tryon_worker.providers["seedream"].generate = (
            lambda paths, prompt, model: image_bytes
        )
        app.state.tryon_worker.run_job(job)
        assert len(app.state.tryons.get(job["id"])["results"]) == 6
        for invalid in ({"person": {}, "garment": payload["garment"]},
                        {"person": {"overhead": asset["id"]}, "garment": payload["garment"]}):
            response = client.post("/api/tryons", json=invalid)
            assert response.status_code == 422


def test_tryon_model_selection(settings, image_bytes):
    settings = settings.model_copy(update={
        "flux_endpoint": "https://api.bfl.ai/v1/flux-2-pro",
        "flux_api_key": SecretStr("test-only"),
        "gpt_image_endpoint": "https://api.openai.com/v1/images/edits",
        "gpt_image_api_key": SecretStr("test-only"),
    })
    app = create_app(settings, start_worker=False)
    capabilities = next(
        route.endpoint for route in app.routes
        if getattr(route, "path", None) == "/api/capabilities"
    )
    assert capabilities()["tryon_providers"] == {
        "seedream": False, "flux": True, "flux_max": False, "flux_klein": False,
        "flux_klein_9b": False, "gpt_image": True,
    }
    with fund_client(TestClient(app, base_url="http://localhost:8000")) as client:
        asset = client.post("/api/assets", files={"file": ("a.png", image_bytes)}).json()
        for provider, model in (("flux", "flux-2-pro"), ("gpt_image", "gpt-image-2")):
            created = client.post("/api/tryons", json={
                "provider": provider,
                "person": {"front": asset["id"]},
                "garment": {"front": asset["id"]},
            })
            assert created.status_code == 201, created.text
            job = created.json()
            assert job["model"] == model
            assert job["provider"] == provider


def test_missing_angle_uses_nearest_reference():
    available = {"front": "a", "back": "b", "right_front": "c"}
    assert closest_view(available, "left") == "front"
    assert closest_view(available, "right") == "right_front"
    assert closest_view(available, "back") == "back"


@pytest.mark.parametrize("references", [2, 8])
def test_flux_max_async_api_uses_own_key_and_downloads_result(
    settings, store, image_bytes, monkeypatch, references,
):
    settings = settings.model_copy(update={
        "flux_api_key": SecretStr("pro-key-must-not-be-used"),
        "flux_max_endpoint": "https://api.bfl.ai/v1/flux-2-max",
        "flux_max_api_key": SecretStr("max-test-key"),
    })
    requests = []
    polls = []

    def handle(request):
        import json

        requests.append(request)
        if request.method == "POST":
            assert str(request.url) == settings.flux_max_endpoint
            assert request.headers["x-key"] == "max-test-key"
            payload = json.loads(request.content)
            assert payload["prompt"] == "Keep identity, change clothing"
            assert "model" not in payload  # BFL chooses the model by endpoint.
            image_keys = {key for key in payload if key.startswith("input_image")}
            assert len(image_keys) == references
            assert "input_image" in image_keys
            return httpx.Response(200, json={"id": "max-task",
                "polling_url": "https://api.bfl.ai/v1/get_result?id=max-task"})
        if request.url.path == "/v1/get_result":
            assert request.headers["x-key"] == "max-test-key"
            assert request.url.params["id"] == "max-task"
            polls.append(request)
            if len(polls) == 1:
                return httpx.Response(200, json={"status": "Pending"})
            return httpx.Response(200, json={"status": "Ready", "result": {
                "sample": "https://images.example.com/max-result.png",
            }})
        assert "x-key" not in request.headers
        return httpx.Response(200, content=image_bytes)

    monkeypatch.setattr("itp.tryon.time.sleep", lambda _: None)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    provider = FluxProvider(settings, client, provider="flux_max")
    path = store.path(store.test_image)
    assert provider.generate([path] * references, "Keep identity, change clothing",
                             "flux-2-max") == image_bytes
    assert len(polls) == 2 and len(requests) == 4
    with pytest.raises(ValueError, match="1–8"):
        provider.generate([path] * 9, "prompt", "flux-2-max")


def test_flux_max_rejects_untrusted_polling_address(settings, store):
    settings = settings.model_copy(update={
        "flux_max_endpoint": "https://api.bfl.ai/v1/flux-2-max",
        "flux_max_api_key": SecretStr("max-test-key"),
    })

    def handle(request):
        assert request.method == "POST"
        return httpx.Response(200, json={"id": "max-task", "polling_url":
            "https://untrusted.example/v1/get_result?id=max-task"})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    with pytest.raises(ValueError, match="轮询地址不可信"):
        FluxProvider(settings, client, provider="flux_max").generate(
            [store.path(store.test_image)], "prompt", "flux-2-max",
        )


def test_flux_and_gpt_image_request_shapes(settings, store, image_bytes):
    path = store.path(store.test_image)
    flux_settings = settings.model_copy(update={
        "flux_endpoint": "https://api.bfl.ai/v1/flux-2-pro",
        "flux_api_key": SecretStr("test-only"),
    })

    class FluxClient:
        def post(self, url, json, headers):
            assert url == flux_settings.flux_endpoint
            assert headers["x-key"] == "test-only"
            assert "input_image_2" in json
            return httpx.Response(200, json={"id": "task-1", "polling_url": "https://api.bfl.ai/v1/get_result?id=task-1"}, request=httpx.Request("POST", url))

        def get(self, url, **kwargs):
            if "get_result" in url:
                assert kwargs["params"] is None
                return httpx.Response(200, json={"status": "Ready", "result": {"sample": "https://example.com/result.png"}}, request=httpx.Request("GET", url))
            return httpx.Response(200, content=image_bytes, request=httpx.Request("GET", url))

    assert FluxProvider(flux_settings, FluxClient()).generate([path, path], "prompt", "flux-2-pro") == image_bytes

    gpt_settings = settings.model_copy(update={
        "gpt_image_endpoint": "https://api.openai.com/v1/images/edits",
        "gpt_image_api_key": SecretStr("test-only"),
    })

    class GPTClient:
        def post(self, url, json, headers):
            assert json["model"] == "gpt-image-2"
            assert len(json["images"]) == 2
            assert json["images"][0]["image_url"].startswith("data:image/")
            assert headers["Authorization"] == "Bearer test-only"
            import base64
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image_bytes).decode()}]}, request=httpx.Request("POST", url))

    assert GPTImageProvider(gpt_settings, GPTClient()).generate([path, path], "prompt", "gpt-image-2") == image_bytes


@pytest.mark.parametrize("variant", ["flux_klein", "flux_klein_9b"])
def test_flux_klein_request_shape_and_reference_limit(settings, store, image_bytes, variant):
    import base64

    settings = settings.model_copy(update={
        f"{variant}_endpoint": "http://127.0.0.1:8789/v1/flux-klein/edit",
        f"{variant}_api_key": SecretStr("test-only"),
    })
    path = store.path(store.test_image)

    class KleinClient:
        def post(self, url, json, headers):
            assert url == getattr(settings, f"{variant}_endpoint")
            assert headers["Authorization"] == "Bearer test-only"
            assert json["model"] == settings.tryon_model_for(variant)
            assert len(json["images"]) == 4
            assert all(image.startswith("data:image/jpeg;base64,") for image in json["images"])
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image_bytes).decode()}]},
                                  request=httpx.Request("POST", url))

    provider = FluxKleinProvider(settings, KleinClient(), provider=variant)
    assert provider.generate([path] * 4, "prompt", settings.tryon_model_for(variant)) == image_bytes
    with pytest.raises(ValueError, match="最多支持四张"):
        provider.generate([path] * 5, "prompt", settings.tryon_model_for(variant))


@pytest.mark.parametrize("variant", ["flux_klein", "flux_klein_9b"])
def test_flux_klein_worker_uses_at_most_four_references(settings, store, image_bytes, variant):
    request = TryOnRequest(provider=variant,
                           person={view: store.test_image for view in VIEWS},
                           garment={view: store.test_image for view in VIEWS})
    jobs = TryOnStore(store.root)
    job = jobs.create(request, settings.tryon_model_for(variant))
    worker = TryOnWorker(store, jobs, settings)
    calls = []

    def generate(paths, prompt, model):
        calls.append((len(paths), prompt, model))
        return image_bytes

    worker.providers[variant].generate = generate
    worker.run_job(job)
    assert jobs.get(job["id"])["state"] == "ready"
    assert [call[0] for call in calls] == [2, 4, 4, 4, 4, 4]
    assert "图3是已生成的正面换装图" in calls[1][1]


@pytest.mark.parametrize("variant,remote_model,ready", [
    ("flux_klein", "flux.2-klein-4b", True),
    ("flux_klein_9b", "flux.2-klein-9b", True),
    ("flux_klein_9b", "flux.2-klein-4b", False),
])
def test_flux_klein_job_requires_loaded_remote_model(
    settings, image_bytes, monkeypatch, variant, remote_model, ready,
):
    settings = settings.model_copy(update={
        f"{variant}_endpoint": "http://127.0.0.1:8788/v1/flux-klein/edit",
        f"{variant}_api_key": SecretStr("test-only"),
    })
    app = create_app(settings, start_worker=False)
    health_path = "flux-klein-9b" if variant == "flux_klein_9b" else "flux-klein"
    health = next(route.endpoint for route in app.routes
                  if getattr(route, "path", None) == f"/api/tryon-providers/{health_path}/health")

    class HealthClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers):
            assert url == "http://127.0.0.1:8788/health"
            assert headers["Authorization"] == "Bearer test-only"
            return httpx.Response(200, json={"ready": True, "model": remote_model},
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr("itp.api.httpx.Client", HealthClient)
    assert health()["ready"] is ready
    with fund_client(TestClient(app, base_url="http://localhost:8000")) as client:
        asset = client.post("/api/assets", files={"file": ("a.png", image_bytes)}).json()
        created = client.post("/api/tryons", json={
            "provider": variant,
            "person": {"front": asset["id"]},
            "garment": {"front": asset["id"]},
        })
        if not ready:
            assert created.status_code == 503
            assert app.state.tryons.list() == []
        else:
            assert created.status_code == 201, created.text
            assert created.json()["model"] == settings.tryon_model_for(variant)


@pytest.mark.parametrize("provider", ["seedream", "flux", "flux_max", "gpt_image"])
def test_partial_views_generate_six_assets_without_http(settings, store, image_bytes, provider):
    request = TryOnRequest(person={"left": store.test_image}, garment={"back": store.test_image}, provider=provider)
    request.validate_views()
    jobs = TryOnStore(store.root)
    job = jobs.create(request, settings.tryon_model_for(provider))
    worker = TryOnWorker(store, jobs, settings)
    calls = []

    def generate(paths, prompt, model):
        calls.append((len(paths), prompt, model))
        return image_bytes

    worker.providers[provider].generate = generate
    worker.run_job(job)
    ready = jobs.get(job["id"])
    assert ready["state"] == "ready"
    assert set(ready["results"]) == set(VIEWS)
    assert [item[0] for item in calls] == [2, 5, 5, 5, 5, 5]
    assert "人物左侧原图" in calls[0][1]
    assert "服装背面原图" in calls[0][1]

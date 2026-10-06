"""Durable six-view virtual try-on jobs backed by the mainland Ark image API."""

import base64
import binascii
import json
import logging
import ipaddress
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from itp.config import BFL_PROVIDERS, HAIJING_GENERATION_ENDPOINT, KLEIN_PROVIDERS, Settings
from itp.image_relay import generate_haijing
from itp.preprocessing import MAX_UPLOAD, image_base64, prepare_image
from itp.storage import Store

logger = logging.getLogger(__name__)
VIEWS = ("front", "back", "left", "right", "left_front", "right_front")
LABELS = ("正面", "背面", "左侧", "右侧", "左前45度", "右前45度")
VIEW_LABELS = dict(zip(VIEWS, LABELS, strict=True))
VIEW_AZIMUTH = {"front": 0, "back": 180, "left": -90, "right": 90,
                "left_front": -45, "right_front": 45}
PROVIDERS = ("seedream", *BFL_PROVIDERS, *KLEIN_PROVIDERS, "gpt_image")


def closest_view(available: dict[str, str], target: str) -> str:
    """Choose the uploaded camera direction nearest a requested output view."""
    angle = VIEW_AZIMUTH[target]
    return min(available, key=lambda view: (abs((VIEW_AZIMUTH[view] - angle + 180) % 360 - 180),
                                            VIEWS.index(view)))


class TryOnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="虚拟试穿", min_length=1, max_length=80)
    person: dict[str, str]
    garment: dict[str, str]
    provider: str = "seedream"
    consistent_confirmed: bool = False

    def validate_views(self):
        if self.provider not in PROVIDERS:
            raise ValueError("不支持的试穿生图模型")
        for group in (self.person, self.garment):
            if not group or not set(group).issubset(VIEWS):
                raise ValueError("人物和服装各需至少一张、至多六张有效视角图片")
            if any(
                len(value) != 32 or any(char not in "0123456789abcdef" for char in value)
                for value in group.values()
            ):
                raise ValueError("图片资产 ID 无效")


class TryOnStore:
    def __init__(self, root: Path):
        self.db = root / "tryons.sqlite3"
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tryons "
                "(id TEXT PRIMARY KEY, state TEXT, created REAL, document TEXT)"
            )

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def save(self, job: dict):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tryons VALUES (?, ?, ?, ?)",
                (job["id"], job["state"], job["created"], json.dumps(job)),
            )

    def create(self, request: TryOnRequest, model: str, owner_id=None) -> dict:
        job = {
            "id": uuid4().hex,
            "name": request.name,
            "created": time.time(),
            "state": "queued",
            "model": model,
            "provider": request.provider,
            "request": request.model_dump(),
            "results": {},
            "active_view": None,
            "error": None,
            "owner_id": owner_id,
        }
        self.save(job)
        return job

    def get(self, job_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT document FROM tryons WHERE id = ?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT document FROM tryons ORDER BY created DESC LIMIT 100"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]


ARK_ERROR_HINTS = {
    "AuthenticationError": "API Key 无效或未授权；请核对方舟密钥",
    "AccessDenied": "账号无权限或服务未开通；请检查方舟控制台的开通状态",
    "ModelNotOpen": "模型尚未开通；请在方舟控制台开通该模型",
    "ModelNotFound": "模型不可用；请核对模型 ID 与服务地域",
    "InvalidParameter": "请求参数不被接受；请核对图片、模型版本和生成选项",
    "AccountOverdueError": "账号欠费；请检查方舟账户余额与账单",
    "QuotaExceeded": "额度不足；请检查方舟配额",
    "RateLimitExceeded": "请求触发限流；请稍后再试",
}


def ark_error_summary(response: httpx.Response) -> str:
    """Describe a failed Ark response with its status, error code and message."""
    code, message = "", ""
    try:
        error = response.json().get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")[:100]
            message = str(error.get("message") or "")[:200]
    except ValueError:
        pass
    hint = ARK_ERROR_HINTS.get(code) or {
        401: "鉴权失败；请核对方舟 API Key",
        403: "账号无权限或服务未开通；请检查方舟控制台",
        404: "模型不可用；请核对模型 ID 与地域",
        429: "请求触发限流或超出配额；请稍后再试",
    }.get(response.status_code, "请在方舟控制台核对错误码、账号权限和模型配置")
    detail = " ".join(part for part in (f"HTTP {response.status_code}", code, message) if part)
    return f"{detail}：{hint}"


class SeedDreamProvider:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(
            timeout=180, follow_redirects=False, trust_env=False
        )

    def generate(self, image_paths: list[Path], prompt: str, model: str) -> bytes:
        if not self.settings.tryon_ready:
            raise RuntimeError("SeedDream API 尚未配置")
        payload = {
            "model": model,
            "prompt": prompt,
            "image": [image_base64(path, data_url=True) for path in image_paths],
            "size": "2K",
            "response_format": "b64_json",
            "watermark": False,
        }
        try:
            response = self.client.post(
                self.settings.seedream_endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.settings.seedream_api_key.get_secret_value()}"
                },
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"SeedDream 生成失败（{ark_error_summary(response)}）；"
                    "请检查服务配置和控制台任务记录"
                )
            data = response.json()["data"][0]["b64_json"]
            raw = base64.b64decode(data, validate=True)
        except (httpx.HTTPError, KeyError, IndexError, ValueError, binascii.Error) as exc:
            raise RuntimeError(
                f"SeedDream 生成失败（{type(exc).__name__}）；请检查服务配置和控制台任务记录"
            ) from None
        if len(raw) > MAX_UPLOAD:
            raise ValueError("SeedDream 返回图片超过 10 MiB")
        return raw


class FluxProvider:
    """FLUX.2 Pro/Max editing via BFL or the Haijing trial protocol."""

    def __init__(
        self, settings: Settings, client: httpx.Client | None = None,
        *, provider: str = "flux",
    ):
        if provider not in BFL_PROVIDERS:
            raise ValueError("Unsupported BFL API provider")
        self.provider = provider
        self.label = "FLUX.2 Max" if provider == "flux_max" else "FLUX.2 Pro"
        self.settings = settings
        self.client = client or httpx.Client(timeout=180, follow_redirects=False, trust_env=False)

    def generate(self, image_paths: list[Path], prompt: str, model: str) -> bytes:
        if not self.settings.tryon_provider_ready(self.provider):
            raise RuntimeError(f"{self.label} API 尚未配置")
        if not 1 <= len(image_paths) <= 8:
            raise ValueError("FLUX 支持 1–8 张参考图")
        endpoint = getattr(self.settings, f"{self.provider}_endpoint")
        api_key = getattr(self.settings, f"{self.provider}_api_key").get_secret_value()
        if endpoint == HAIJING_GENERATION_ENDPOINT:
            return generate_haijing(self.client, endpoint, api_key, image_paths, prompt, model)
        payload = {"prompt": prompt}
        for index, path in enumerate(image_paths):
            payload["input_image" if index == 0 else f"input_image_{index + 1}"] = image_base64(path)
        headers = {"x-key": api_key}
        try:
            response = self.client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
            task_id = body["id"]
            poll_url = body.get("polling_url") or f"{urlparse(endpoint).scheme}://{urlparse(endpoint).netloc}/v1/get_result"
            parsed_poll = urlparse(poll_url)
            if (parsed_poll.netloc != urlparse(endpoint).netloc or parsed_poll.scheme != "https"
                    or parsed_poll.path != "/v1/get_result" or parsed_poll.fragment
                    or (parsed_poll.query and parse_qs(parsed_poll.query) != {"id": [task_id]})):
                raise ValueError("FLUX 返回的轮询地址不可信")
            deadline = time.monotonic() + self.settings.task_timeout_seconds
            while time.monotonic() < deadline:
                result = self.client.get(poll_url, params=None if parsed_poll.query else {"id": task_id}, headers=headers)
                result.raise_for_status()
                status = result.json()
                if status.get("status") == "Ready":
                    sample_url = status["result"]["sample"]
                    parsed = urlparse(sample_url)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                        raise ValueError("FLUX 返回的图片地址不可信")
                    if parsed.hostname in {"localhost"} or parsed.hostname.endswith(".local"):
                        raise ValueError("FLUX 返回的图片地址不可信")
                    try:
                        address = ipaddress.ip_address(parsed.hostname)
                    except ValueError:
                        address = None
                    if address is not None and not address.is_global:
                        raise ValueError("FLUX 返回的图片地址不可信")
                    image = self.client.get(sample_url)
                    image.raise_for_status()
                    raw = image.content
                    if len(raw) > MAX_UPLOAD:
                        raise ValueError("FLUX 返回图片超过 10 MiB")
                    return raw
                if status.get("status") in {"Error", "Failed"}:
                    raise RuntimeError(f"{self.label} 云端任务失败")
                if status.get("status") not in {"Pending", "Processing", "Queued"}:
                    raise RuntimeError(f"{self.label} 返回未知任务状态")
                time.sleep(self.settings.poll_seconds)
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise RuntimeError(f"{self.label} 调用失败（{type(exc).__name__}）") from exc
        raise RuntimeError(f"{self.label} 生成超时；请在服务控制台确认任务状态")


class GPTImageProvider:
    """OpenAI-compatible Images Edit API with multi-image inputs."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=180, follow_redirects=False, trust_env=False)

    def generate(self, image_paths: list[Path], prompt: str, model: str) -> bytes:
        if not self.settings.tryon_provider_ready("gpt_image"):
            raise RuntimeError("GPT Image API 尚未配置")
        payload = {"model": model, "prompt": prompt,
                   "images": [{"image_url": image_base64(path, data_url=True)} for path in image_paths]}
        try:
            response = self.client.post(
                self.settings.gpt_image_endpoint, json=payload,
                headers={"Authorization": f"Bearer {self.settings.gpt_image_api_key.get_secret_value()}"},
            )
            response.raise_for_status()
            raw = base64.b64decode(response.json()["data"][0]["b64_json"], validate=True)
        except (httpx.HTTPError, KeyError, IndexError, ValueError, binascii.Error) as exc:
            raise RuntimeError(f"GPT Image 生成失败（{type(exc).__name__}）") from exc
        if len(raw) > MAX_UPLOAD:
            raise ValueError("GPT Image 返回图片超过 10 MiB")
        return raw


class FluxKleinProvider:
    """Call a self-hosted FLUX.2 Klein 4B or 9B editing service."""

    def __init__(
        self, settings: Settings, client: httpx.Client | None = None,
        *, provider: str = "flux_klein",
    ):
        if provider not in KLEIN_PROVIDERS:
            raise ValueError("Unsupported FLUX Klein provider")
        self.provider = provider
        self.label = "FLUX.2 Klein 9B" if provider == "flux_klein_9b" else "FLUX.2 Klein 4B"
        self.settings = settings
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(900.0, connect=10.0), follow_redirects=False, trust_env=False
        )

    def generate(self, image_paths: list[Path], prompt: str, model: str) -> bytes:
        if not self.settings.tryon_provider_ready(self.provider):
            raise RuntimeError(f"{self.label} 服务尚未配置")
        if not 1 <= len(image_paths) <= 4:
            raise ValueError(f"{self.label} 最多支持四张参考图")
        payload = {"model": model, "prompt": prompt,
                   "images": [image_base64(path, data_url=True) for path in image_paths]}
        try:
            response = self.client.post(
                getattr(self.settings, f"{self.provider}_endpoint"), json=payload,
                headers={"Authorization": "Bearer " + getattr(
                    self.settings, f"{self.provider}_api_key"
                ).get_secret_value()},
            )
            response.raise_for_status()
            raw = base64.b64decode(response.json()["data"][0]["b64_json"], validate=True)
        except (httpx.HTTPError, KeyError, IndexError, ValueError, binascii.Error) as exc:
            raise RuntimeError(f"{self.label} 调用失败（{type(exc).__name__}）") from exc
        if len(raw) > MAX_UPLOAD:
            raise ValueError(f"{self.label} 返回图片超过 10 MiB")
        return raw


class TryOnWorker:
    def __init__(
        self,
        assets: Store,
        jobs: TryOnStore,
        settings: Settings,
        provider: SeedDreamProvider | None = None,
    ):
        self.assets, self.jobs, self.settings = assets, jobs, settings
        self.provider = provider or SeedDreamProvider(settings)
        self.providers = {"seedream": self.provider, "flux": FluxProvider(settings),
                          "flux_max": FluxProvider(settings, provider="flux_max"),
                          "flux_klein": FluxKleinProvider(settings),
                          "flux_klein_9b": FluxKleinProvider(settings, provider="flux_klein_9b"),
                          "gpt_image": GPTImageProvider(settings)}
        self.stop = threading.Event()

    def run_job(self, job: dict):
        with self.assets.processing(job["id"], job.get("owner_id")):
            self._run_job(job)

    def _run_job(self, job: dict):
        job["state"] = "running"
        self.jobs.save(job)
        try:
            for view, label in zip(VIEWS, LABELS, strict=True):
                self.assets.checkpoint()
                if time.time() - job["created"] > self.settings.task_timeout_seconds:
                    raise ValueError("试穿任务已超时")
                if self.stop.is_set():
                    return
                if view in job["results"]:
                    continue
                job["active_view"] = view
                self.jobs.save(job)
                person_view = closest_view(job["request"]["person"], view)
                garment_view = closest_view(job["request"]["garment"], view)
                person = self.assets.path(job["request"]["person"][person_view])
                garment = self.assets.path(job["request"]["garment"][garment_view])
                references = [person, garment]
                is_klein = job.get("provider") in KLEIN_PROVIDERS
                klein_anchor = None
                if view != "front" and is_klein:
                    references.append(self.assets.path(job["results"]["front"]))
                    person_anchor = closest_view(job["request"]["person"], "front")
                    garment_anchor = closest_view(job["request"]["garment"], "front")
                    if person_anchor != person_view:
                        references.append(self.assets.path(job["request"]["person"][person_anchor]))
                        klein_anchor = "人物"
                    elif garment_anchor != garment_view:
                        references.append(self.assets.path(job["request"]["garment"][garment_anchor]))
                        klein_anchor = "服装"
                elif view != "front":
                    references += [self.assets.path(job["request"]["person"][closest_view(job["request"]["person"], "front")]),
                                   self.assets.path(job["request"]["garment"][closest_view(job["request"]["garment"], "front")]),
                                   self.assets.path(job["results"]["front"])]
                prompt = (
                    f"图1是人物{VIEW_LABELS[person_view]}原图，图2是服装{VIEW_LABELS[garment_view]}原图。只替换人物衣服，输出同一人物的{label}全身照。"
                    "严格保留图1的身份、脸部五官、发型、肤色、体型、姿势、肢体数量和背景；"
                    "严格保留图2的服装版型、面料、纹样、颜色，不改变服装结构。"
                    "光线真实，身体完整，不添加文字、道具或其他人物。"
                    "最后重点检查发型和体型是否严格参照图1。"
                )
                if person_view == view:
                    prompt += "保持图1的镜头角度。"
                else:
                    prompt += f"图1缺少目标视角，请在不改变人物身份与姿势的前提下将镜头调整至{label}。"
                if view != "front" and is_klein:
                    prompt += "图3是已生成的正面换装图；所有视角必须与图3为同一个人、同一套衣服。"
                    if klein_anchor:
                        prompt += f"图4是补充的{klein_anchor}参考图。"
                elif view != "front":
                    prompt += (
                        "图3、图4是补充人物和服装参考，图5是已经生成的换装正面图；"
                        "所有视角必须与图5为同一个人、同一套衣服。"
                    )
                # A crash during a billable request must not silently resubmit that view.
                job["state"] = "submitting"
                self.jobs.save(job)
                raw = self.providers[job.get("provider", "seedream")].generate(references, prompt, job["model"])
                image = prepare_image(raw)
                asset_id, path = self.assets.new_asset_path("png")
                image.save(path, format="PNG")
                self.assets.add_asset(
                    asset_id, path, "image", width=image.width, height=image.height
                )
                job["results"][view] = asset_id
                job["state"] = "running"
                self.jobs.save(job)
            job["state"] = "ready"
            job["active_view"] = None
            self.jobs.save(job)
        except Exception as exc:
            job["state"] = "failed"
            job["error"] = (
                str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "虚拟试穿处理失败"
            )
            self.jobs.save(job)
            logger.warning("Try-on %s failed (%s)", job["id"], type(exc).__name__)
        finally:
            if job["state"] in {"ready", "failed", "cancelled"}:
                self.assets.finish(job["id"], job["results"].values())

    def run_forever(self):
        for job in self.jobs.list():
            if job["state"] in {"running", "submitting"}:
                job["state"] = "failed"
                job["error"] = "进程中断时云端结果不确定；未自动重提，请核对控制台后重新创建任务"
                self.jobs.save(job)
        while not self.stop.is_set():
            for job in self.jobs.list():
                if job["state"] == "queued":
                    self.run_job(job)
            self.stop.wait(0.5)

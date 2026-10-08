import json
import re
from pathlib import Path

import httpx

from itp.config import Settings
from itp.preprocessing import image_base64

# The one cloud action the modelling pipeline still takes: a base mesh that
# previews and downloads.  Retopology, texturing and rigging were removed with
# the rest of the post-processing options, so their endpoints are not reachable
# from here any more.
ACTIONS = {
    "geometry": ("SubmitHunyuanTo3DProJob", "QueryHunyuanTo3DProJob"),
}


class ProviderError(RuntimeError):
    pass


def safe_code(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "", str(value))[:100]


TENCENT_ERROR_HINTS = {
    "ResourceUnavailable.NotExist": (
        "可能是服务未开通或计费状态异常；请在腾讯云控制台核对混元生3D服务与账号状态"
    ),
    "ResourceUnavailable.InArrears": "账号可能欠费；请检查腾讯云账户余额和账单",
    "ResourceUnavailable.LowBalance": "账户余额不足；请检查腾讯云账户余额",
    "AuthFailure.InvalidSecretId": "Secret ID 无效；请在设置页核对云 API 密钥",
    "AuthFailure.SignatureFailure": "签名校验失败；请核对 Secret ID、Secret Key 和系统时间",
    "UnsupportedRegion": "所选地域不支持此接口；请核对腾讯云控制台支持的地域",
    "UnauthorizedOperation": "当前账号无调用权限；请检查 CAM 授权和服务开通状态",
    "RequestLimitExceeded": "请求频率超过限制；请稍后再试",
}

POSE_ERROR_HINTS = {
    "InvalidApiKey": "API Key 无效；请核对密钥和北京地域服务地址",
    "invalid_api_key": "API Key 无效；请核对密钥和北京地域服务地址",
    "AccessDenied.Unpurchased": "百炼服务或模型尚未开通；请检查账号权限",
    "ModelNotFound": "模型不可用；请核对模型名称和业务空间授权",
    "Throttling.RateQuota": "请求触发限流；请稍后再试",
    "Throttling.AllocationQuota": "可用额度不足；请检查百炼配额",
}


def tencent_error_hint(code: str) -> str:
    return TENCENT_ERROR_HINTS.get(code) or (
        "请求参数不被接受；请核对当前步骤的输入与接口要求"
        if code.startswith("InvalidParameter") else
        "腾讯云服务暂不可用；请稍后再试，并凭 RequestId 查询"
    )


def pose_error_hint(code: str, status: int | None = None) -> str:
    if code in POSE_ERROR_HINTS:
        return POSE_ERROR_HINTS[code]
    if code.startswith("Throttling"):
        return "请求触发限流或配额限制；请到百炼控制台检查"
    return {
        400: "请求参数不被接受；请核对模型和输入图片",
        401: "鉴权失败；请核对 API Key 与服务地址",
        403: "账号无权限或服务未开通；请检查业务空间和模型授权",
        404: "模型或服务地址不存在；请核对设置页中的配置",
        429: "请求触发限流；请稍后再试",
        500: "百炼服务出现内部错误；请稍后再试",
        503: "百炼服务暂不可用；请稍后再试",
    }.get(status, "请在百炼控制台核对错误码、账号权限和模型配置")


class TencentProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def call(self, action: str, payload: dict) -> dict:
        if not self.settings.geometry_ready:
            raise ProviderError("腾讯云 API 待配置")
        from tencentcloud.ai3d.v20250513 import ai3d_client, models
        from tencentcloud.common import credential
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
            TencentCloudSDKException,
        )
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile

        http = HttpProfile(endpoint=self.settings.tencent_endpoint, reqTimeout=60)
        profile = ClientProfile(httpProfile=http)
        profile.retryer = None
        client = ai3d_client.Ai3dClient(
            credential.Credential(
                self.settings.tencent_secret_id.get_secret_value(),
                self.settings.tencent_secret_key.get_secret_value(),
            ),
            self.settings.tencent_region,
            profile,
        )
        request = getattr(models, f"{action}Request")()
        request.from_json_string(json.dumps(payload))
        try:
            response = getattr(client, action)(request)
            return json.loads(response.to_json_string())
        except TencentCloudSDKException as exc:
            # Do not persist vendor messages that may contain signed URLs or request data.
            code = safe_code(exc.get_code())
            raise ProviderError(
                f"腾讯云错误 {code}：{tencent_error_hint(code)}；"
                f"RequestId={safe_code(exc.get_request_id() or '')}"
            ) from None

    def submit(self, stage: str, payload: dict) -> dict:
        return self.call(ACTIONS[stage][0], payload)

    def query(self, stage: str, job_id: str) -> dict:
        return self.call(ACTIONS[stage][1], {"JobId": job_id})

class PoseProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def edit(self, character: Path, reference: Path | None, mode: str, seed: int,
             model: str | None = None) -> dict:
        if not self.settings.pose_ready:
            raise ProviderError("姿势编辑 API 待配置")
        content = [{"image": image_base64(character, data_url=True)}]
        if reference:
            content.append({"image": image_base64(reference, data_url=True)})
            stance = "Match only the body pose and limb directions of the person in image 2."
        else:
            stance = (
                "Use a symmetric T-pose with both arms extended horizontally."
                if mode == "t-pose"
                else "Use a symmetric A-pose with arms angled down 45 degrees from the torso."
            )
        content.append(
            {
                "text": (
                    "Show one full-body character from image 1 on a plain white background. "
                    "Preserve identity, face, clothing, colors and proportions from image 1. "
                    f"{stance} Keep hands and feet in frame, separate limbs clearly. "
                    "Do not copy identity or clothing from image 2. "
                    "No captions, panels or extra people."
                )
            }
        )
        payload = {
            "model": model or self.settings.pose_model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {
                "n": 1,
                "seed": seed,
                "watermark": False,
                "prompt_extend": False,
                "size": "1024*1024",
            },
        }
        with httpx.Client(timeout=180, trust_env=False) as client:
            response = client.post(
                self.settings.pose_endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.settings.pose_api_key.get_secret_value()}"
                },
            )
        if response.status_code != 200:
            try:
                body = response.json()
                code = safe_code(body.get("code", "")) if isinstance(body, dict) else ""
            except ValueError:
                code = ""
            raise ProviderError(
                f"姿势 API 错误 {code or f'HTTP{response.status_code}'}："
                f"{pose_error_hint(code, response.status_code)}"
            )
        data = response.json()
        if data.get("code"):
            code = safe_code(data["code"])
            raise ProviderError(f"姿势 API 错误 {code}：{pose_error_hint(code)}")
        try:
            result = data["output"]["choices"][0]["message"]["content"]
            url = next(item["image"] for item in result if "image" in item)
        except (KeyError, IndexError, StopIteration, TypeError) as exc:
            raise ProviderError("姿势 API 未返回图片") from exc
        return {"url": url, "request_id": data.get("request_id")}

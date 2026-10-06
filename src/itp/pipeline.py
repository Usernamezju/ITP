import logging
import threading
import time

from itp.config import Settings
from itp.downloads import download
from itp.preprocessing import image_base64, prepare_image
from itp.providers import PoseProvider, ProviderError, TencentProvider, safe_code
from itp.storage import Store
from itp.model_validation import valid_mesh

logger = logging.getLogger(__name__)


class Interrupted(Exception):
    pass


class Pipeline:
    def __init__(self, store: Store, settings: Settings, cloud=None, pose=None, fetch=None, commerce=None):
        self.store = store
        self.settings = settings
        self.cloud = cloud or TencentProvider(settings)
        self.pose = pose or PoseProvider(settings)
        self.fetch = fetch or download
        self.stop = threading.Event()
        self.commerce = commerce

    def checkpoint(self):
        if self.stop.is_set():
            raise Interrupted

    def has_valid_result(self, job):
        return any(valid_mesh(self.store.path(artifact["asset_id"]), artifact["format"])
                   for artifact in job["artifacts"] if self.store.asset(artifact["asset_id"]))

    def step(self, job: dict, name: str) -> dict:
        existing = next((s for s in job["steps"] if s["name"] == name), None)
        if existing:
            return existing
        step = {
            "name": name,
            "status": "pending",
            "started": time.time(),
            "provider_job_id": None,
            "request_id": None,
            "results": [],
        }
        job["steps"].append(step)
        self.store.save_job(job)
        return step

    def persist_results(self, job: dict, step: dict):
        for index, result in enumerate(step["results"]):
            self.checkpoint()
            if any(a["stage"] == step["name"] and a["index"] == index for a in job["artifacts"]):
                continue
            kind = result.get("Type", "").upper()
            if kind not in {"GLB", "OBJ", "FBX", "ZIP", "MTL", "PNG", "JPG", "JPEG"}:
                continue
            asset_id, path = self.store.new_asset_path(kind.lower())
            try:
                self.fetch(result["Url"], path)
                with path.open("rb") as handle:
                    magic = handle.read(12)
                if magic.startswith(b"PK\x03\x04") and kind != "ZIP":
                    actual = path.with_suffix(".zip")
                    path.replace(actual)
                    path, kind = actual, "ZIP"
                if kind == "GLB" and magic[:4] != b"glTF":
                    raise ValueError("供应商返回的 GLB 文件头无效")
                self.store.add_asset(asset_id, path, "model", format=kind)
                job["artifacts"].append(
                    {"asset_id": asset_id, "format": kind, "stage": step["name"], "index": index}
                )
                self.store.save_job(job)
            except Exception:
                if not self.store.asset(asset_id):
                    path.unlink(missing_ok=True)
                raise

    def cloud_step(self, job: dict, name: str, payload: dict) -> list[dict]:
        self.checkpoint()
        step = self.step(job, name)
        if step["status"] == "done":
            return step["results"]
        if step["status"] == "submitting":
            raise ProviderError("提交结果不确定，已停止自动重提；请在云控制台核对是否已扣费")
        if step["status"] == "pending":
            # Persist before the billable call: a crash cannot trigger an implicit resubmission.
            step["status"] = "submitting"
            self.store.save_job(job)
            response = self.cloud.submit(name, payload)
            if not response.get("JobId"):
                raise ProviderError("供应商未返回任务 ID；请在云控制台核对，勿重复提交")
            step.update(
                status="submitted",
                provider_job_id=response["JobId"],
                request_id=response.get("RequestId"),
            )
            self.store.save_job(job)
        errors = 0
        while step["status"] != "download":
            self.checkpoint()
            if time.time() - step["started"] > self.settings.task_timeout_seconds:
                raise ProviderError("云任务超过本地等待期限；云端可能仍在执行，请凭任务 ID 查询")
            try:
                response = self.cloud.query(name, step["provider_job_id"])
                errors = 0
            except Exception:
                errors += 1
                if errors >= 3:
                    raise ProviderError(
                        "云任务连续查询失败；任务 ID 已保留，请到控制台核对"
                    ) from None
                self.stop.wait(self.settings.poll_seconds)
                continue
            state = response.get("Status")
            step["request_id"] = response.get("RequestId", step["request_id"])
            if state == "FAIL":
                raise ProviderError(
                    f"云任务失败：{safe_code(response.get('ErrorCode', 'Unknown'))}"
                )
            if state == "DONE":
                results = response.get("ResultFile3Ds") or []
                if not results:
                    raise ProviderError("云任务完成但未返回模型产物")
                step.update(status="download", results=results)
                self.store.save_job(job)
                break
            if state not in {"WAIT", "RUN"}:
                raise ProviderError("供应商返回了未知任务状态")
            self.store.save_job(job)
            self.stop.wait(self.settings.poll_seconds)
        self.persist_results(job, step)
        if not any(a["stage"] == name for a in job["artifacts"]):
            raise ProviderError("供应商未返回本工作台支持的产物格式")
        step["status"] = "done"
        self.store.save_job(job)
        return step["results"]

    @staticmethod
    def choose(results: list[dict], formats=("GLB", "OBJ")) -> dict:
        for kind in formats:
            for result in results:
                if result.get("Type", "").upper() == kind and result.get("Url"):
                    return {"Type": kind, "Url": result["Url"]}
        raise ProviderError("上一步没有返回下一阶段支持的模型格式")

    def pose_step(self, job: dict) -> bool:
        req = job["request"]
        if req["pose_mode"] == "original" or job["pose_approved"]:
            return True
        step = self.step(job, "pose")
        if step["status"] == "submitting":
            raise ProviderError("姿势编辑结果不确定，已停止自动重试；请在百炼控制台核对")
        if step["status"] == "pending":
            step["status"] = "submitting"
            self.store.save_job(job)
            result = self.pose.edit(
                self.store.path(req["front"]),
                self.store.path(req["pose_reference"]) if req["pose_reference"] else None,
                req["pose_mode"],
                req["seed"],
                job.get("models", {}).get("pose"),
            )
            step.update(
                status="download",
                results=[{"Type": "PNG", "Url": result["url"]}],
                request_id=result.get("request_id"),
            )
            self.store.save_job(job)
        if step["status"] == "download":
            asset_id, path = self.store.new_asset_path("png")
            try:
                self.fetch(step["results"][0]["Url"], path, limit=10 * 1024 * 1024)
                image = prepare_image(path.read_bytes())
                image.save(path, format="PNG")
                self.store.add_asset(
                    asset_id, path, "image", width=image.width, height=image.height
                )
                job["pose_asset"] = asset_id
                step["status"] = "done"
                self.store.save_job(job)
            except Exception:
                if not self.store.asset(asset_id):
                    path.unlink(missing_ok=True)
                raise
        job["state"] = "awaiting_review"
        self.store.save_job(job)
        return False

    def convert_step(self, job: dict, results: list[dict]):
        step = self.step(job, "export")
        if step["status"] == "done":
            return
        if step["status"] == "submitting":
            raise ProviderError("格式转换结果不确定，请到云控制台核对，未自动重试")
        if step["status"] == "pending":
            source = self.choose(results, ("GLB", "FBX", "OBJ"))
            if source["Type"] == "FBX":
                step.update(status="download", results=[source])
            else:
                step["status"] = "submitting"
                self.store.save_job(job)
                response = self.cloud.convert(source["Url"])
                step.update(status="download", **response)
            self.store.save_job(job)
        self.persist_results(job, step)
        step["status"] = "done"
        self.store.save_job(job)

    def run_job(self, job: dict):
        try:
            job["state"] = "running"
            self.store.save_job(job)
            self.checkpoint()
            if not self.pose_step(job):
                return
            req = job["request"]
            front = self.store.path(job["pose_asset"] or req["front"])
            payload = {
                "Model": job.get("models", {}).get("geometry") or self.settings.tencent_model,
                "GenerateType": "Geometry",
                "ImageBase64": image_base64(front),
                "FaceCount": req["face_count"],
            }
            if req["views"]:
                payload["MultiViewImages"] = [
                    {"ViewType": view, "ViewImageBase64": image_base64(self.store.path(asset_id))}
                    for view, asset_id in req["views"].items()
                ]
            results = self.cloud_step(job, "geometry", payload)
            if req["topology"]:
                results = self.cloud_step(
                    job,
                    "topology",
                    {
                        "File3D": self.choose(results),
                        "PolygonType": req["polygon_type"],
                        "FaceLevel": req["face_level"],
                    },
                )
            if req["texture"]:
                results = self.cloud_step(
                    job,
                    "texture",
                    {
                        "File3D": self.choose(results),
                        "EnablePBR": True,
                        "Image": {"Base64": image_base64(front)},
                        "TextureSize": req["texture_size"],
                    },
                )
            if req["rig"]:
                results = self.cloud_step(
                    job, "rig", {"File3D": self.choose(results, ("GLB", "FBX"))}
                )
            if req["export_fbx"]:
                self.convert_step(job, results)
            job["state"] = "succeeded"
            self.store.save_job(job)
        except Interrupted:
            return
        except Exception as exc:
            job["state"] = "failed"
            job["error"] = (
                str(exc)
                if isinstance(exc, ProviderError)
                else (
                    f"本地处理失败（{type(exc).__name__}）；检查网络、文件与配置。已保存的产物仍可下载。"
                )
            )
            if job["steps"] and job["steps"][-1]["status"] != "done":
                job["steps"][-1]["status"] = "failed"
            self.store.save_job(job)
            logger.warning("Job %s failed (%s)", job["id"], type(exc).__name__)
        finally:
            if self.commerce and job["state"] in {"succeeded", "failed", "rejected", "cancelled"}:
                self.commerce.finish_model(job["id"], succeeded=job["state"] == "succeeded", valid_result=self.has_valid_result(job))

    def run_forever(self):
        while not self.stop.is_set():
            for expired in self.store.expired_reviews(time.time() - self.settings.task_timeout_seconds):
                try:
                    self.store.review(expired["id"], False)
                except (ValueError, KeyError):
                    continue
                if self.commerce:
                    self.commerce.finish_model(expired["id"], succeeded=False, valid_result=False)
            jobs = self.store.jobs(active=True)
            for job in jobs:
                if self.stop.is_set():
                    return
                self.run_job(job)
            self.stop.wait(0.5)

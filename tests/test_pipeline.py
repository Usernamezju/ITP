import time

import pytest

from itp.pipeline import Pipeline
from itp.providers import ProviderError
from itp.schemas import JobRequest
from itp.storage import public_job


class Cloud:
    def __init__(self):
        self.submissions = []
        self.queries = []
        self.fail = False

    def submit(self, stage, payload):
        self.submissions.append((stage, payload))
        return {"JobId": f"job-{stage}", "RequestId": "request-submit"}

    def query(self, stage, job_id):
        self.queries.append((stage, job_id))
        if self.fail:
            return {"Status": "FAIL", "ErrorCode": "InvalidParameter"}
        return {
            "Status": "DONE",
            "ResultFile3Ds": [
                {"Type": "GLB", "Url": f"https://test.myqcloud.com/{stage}.glb?secret=signature"}
            ],
            "RequestId": "request-query",
        }

    def convert(self, url):
        return {"results": [{"Type": "FBX", "Url": "https://test.myqcloud.com/final.fbx"}]}


def fetch(url, path, **kwargs):
    from test_commerce import triangle_glb
    path.write_bytes(triangle_glb())


def create_job(store, **kwargs):
    return store.create_job(JobRequest(front=store.test_image, **kwargs).model_dump())


def test_pipeline_runs_only_geometry_and_persists_preview_glb(store, settings):
    cloud = Cloud()
    job = create_job(store)
    Pipeline(store, settings, cloud=cloud, fetch=fetch).run_job(job)
    result = store.job(job["id"])
    assert result["state"] == "succeeded"
    assert [s[0] for s in cloud.submissions] == ["geometry"]
    assert len(result["artifacts"]) == 1
    assert "signature" not in str(public_job(result))
    assert all(store.path(a["asset_id"]).exists() for a in result["artifacts"])


def test_geometry_uses_model_saved_with_job(store, settings):
    job = store.create_job(
        JobRequest(front=store.test_image, texture=False).model_dump(),
        models={"geometry": "3.0"},
    )
    cloud = Cloud()
    Pipeline(store, settings, cloud=cloud, fetch=fetch).run_job(job)
    assert cloud.submissions[0][1]["Model"] == "3.0"
    assert store.job(job["id"])["models"]["geometry"] == "3.0"


def test_pose_review_stops_before_geometry_and_resumes(store, settings, image_bytes):
    class Pose:
        calls = 0

        def edit(self, *args):
            self.calls += 1
            return {"url": "https://test.aliyuncs.com/pose.png"}

    def download(url, path, **kwargs):
        if "pose.png" in url:
            path.write_bytes(image_bytes)
        else:
            fetch(url, path)

    cloud, pose = Cloud(), Pose()
    job = create_job(store, pose_mode="custom", pose_reference=store.test_image, texture=False)
    pipeline = Pipeline(store, settings, cloud=cloud, pose=pose, fetch=download)
    pipeline.run_job(job)
    result = store.job(job["id"])
    assert result["state"] == "awaiting_review" and result["pose_asset"]
    assert cloud.submissions == []
    approved = store.review(job["id"], True)
    pipeline.run_job(approved)
    assert store.job(job["id"])["state"] == "succeeded"
    assert pose.calls == 1
    with pytest.raises(ValueError):
        store.review(job["id"], True)


def test_known_remote_job_resumes_without_resubmission(store, settings):
    job = create_job(store, texture=False)
    job["steps"] = [
        {
            "name": "geometry",
            "status": "submitted",
            "started": time.time(),
            "provider_job_id": "existing-id",
            "request_id": None,
            "results": [],
        }
    ]
    cloud = Cloud()
    Pipeline(store, settings, cloud=cloud, fetch=fetch).run_job(job)
    assert cloud.submissions == []
    assert cloud.queries == [("geometry", "existing-id")]
    assert store.job(job["id"])["state"] == "succeeded"


def test_ambiguous_submission_is_not_repeated(store, settings):
    job = create_job(store, texture=False)
    job["steps"] = [
        {
            "name": "geometry",
            "status": "submitting",
            "started": time.time(),
            "provider_job_id": None,
            "results": [],
        }
    ]
    cloud = Cloud()
    Pipeline(store, settings, cloud=cloud, fetch=fetch).run_job(job)
    assert not cloud.submissions
    assert store.job(job["id"])["state"] == "failed"
    assert "提交结果不确定" in store.job(job["id"])["error"]


def test_failure_prevents_downstream_stages(store, settings):
    job = create_job(store)
    cloud = Cloud()
    cloud.fail = True
    Pipeline(store, settings, cloud=cloud, fetch=fetch).run_job(job)
    assert [s[0] for s in cloud.submissions] == ["geometry"]
    assert store.job(job["id"])["state"] == "failed"


def test_transient_queries_retry_without_new_submission(store, settings):
    class Flaky(Cloud):
        attempts = 0

        def query(self, stage, job_id):
            self.attempts += 1
            if self.attempts < 3:
                raise ProviderError("Temporary error")
            return super().query(stage, job_id)

    cloud = Flaky()
    job = create_job(store, texture=False)
    Pipeline(store, settings, cloud=cloud, fetch=fetch).run_job(job)
    assert cloud.attempts == 3 and len(cloud.submissions) == 1
    assert store.job(job["id"])["state"] == "succeeded"


def test_download_failure_does_not_forge_success(store, settings):
    def bad_download(*args, **kwargs):
        raise OSError("https://secret.example.com?key=secret-value")

    job = create_job(store, texture=False)
    Pipeline(store, settings, cloud=Cloud(), fetch=bad_download).run_job(job)
    result = store.job(job["id"])
    assert result["state"] == "failed" and result["artifacts"] == []
    assert "secret-value" not in result["error"]

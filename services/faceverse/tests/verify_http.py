"""Integration acceptance for the real authenticated ClothiNation HTTP protocol."""

import argparse
import base64
import json
import sys
from pathlib import Path

import httpx

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
from itp_faceverse_service.api import OPERATIONS, _validate_glb  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("body_glb", type=Path)
    parser.add_argument("photo", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8787/v1/face-refine")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    payload = {
        "model": "faceverse-v4",
        "mesh_glb_base64": base64.b64encode(args.body_glb.read_bytes()).decode(),
        "face_photo_base64": base64.b64encode(args.photo.read_bytes()).decode(),
        "preserve": ["hair", "back_head", "neck"],
        "alignment_landmarks": ["eyes", "nose_tip", "mouth_corners", "chin", "head_width"],
        "required_operations": list(OPERATIONS),
    }
    with httpx.Client(timeout=600) as client:
        response = client.post(
            args.endpoint,
            headers={"Authorization": f"Bearer {args.token}"},
            json=payload,
        )
    response.raise_for_status()
    result = response.json()
    output = base64.b64decode(result["glb_base64"], validate=True)
    _validate_glb(output)
    report = result["report"]
    if set(report["operations"]) != set(OPERATIONS):
        raise AssertionError("ClothiNation operation report is incomplete")
    print(
        json.dumps(
            {
                "status": response.status_code,
                "glb_bytes": len(output),
                "face_bbox": report["face_bbox"],
                "operations": len(report["operations"]),
                "quality": report["quality"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

"""Prepare the AutoDL production configuration without printing provider secrets."""

import argparse
import os
import secrets
from pathlib import Path

from itp.config import Settings
from itp.provider_settings import save_provider_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    app_dir = Path("/root/autodl-tmp/itp-app")
    data_dir = Path("/root/autodl-tmp/itp-data")
    env_file = app_dir / ".env"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)

    # Both model services run on this host as loopback listeners, so the
    # endpoints are plain HTTP and the bearer tokens never leave the container.
    # The tokens are root-only files outside this repository; the supervisor
    # launcher reads them at start.
    flux_token = Path("/root/autodl-tmp/itp-flux-klein-service/.token")
    faceverse_token = data_dir / "faceverse.token"
    for token_file in (flux_token, faceverse_token):
        if not token_file.is_file():
            token_file.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
            os.chmod(token_file, 0o600)
    save_provider_settings(env_file, {
        "flux_klein_endpoint": "http://127.0.0.1:8788/v1/flux-klein/edit",
        "flux_klein_api_key": flux_token.read_text(encoding="utf-8").strip(),
        "faceverse_endpoint": "http://127.0.0.1:8787/v1/face-refine",
        "faceverse_api_key": faceverse_token.read_text(encoding="utf-8").strip(),
    })
    content = env_file.read_text(encoding="utf-8")
    names = {"ITP_PUBLIC_ORIGIN", "ITP_DATA_DIR", "ITP_SEGMENTATION_MODEL"}
    lines = [line for line in content.splitlines() if line.split("=", 1)[0].strip() not in names]
    lines.extend([
        f"ITP_PUBLIC_ORIGIN={args.origin}",
        f"ITP_DATA_DIR={data_dir}",
        f"ITP_SEGMENTATION_MODEL={app_dir / 'models/u2netp.onnx'}",
    ])
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    # Fail before opening a public listener if required provider configuration is absent.
    settings = Settings(_env_file=env_file)
    if not (settings.geometry_ready and settings.tryon_ready and settings.pose_ready):
        raise SystemExit("Core provider configuration is incomplete")

    print("Production configuration validated; credentials stored on server")


if __name__ == "__main__":
    main()

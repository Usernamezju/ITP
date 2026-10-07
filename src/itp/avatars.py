"""Account avatars: one small, re-encoded profile picture per account.

An avatar is deliberate profile data rather than analysis material, so it is
kept for the account instead of the browser.  Every upload is decoded and
re-encoded on the server, which caps the pixel count, converts to PNG and drops
EXIF (including GPS) before anything reaches the disk.  The picture is served
under a random key, so no account id is published and replacing it invalidates
the previous URL.
"""

import io
import os
import re
import tempfile
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

KEY_PATTERN = re.compile(r"^[a-f0-9]{32}$")
MAX_AVATAR_UPLOAD = 4 * 1024 * 1024
MAX_AVATAR_PIXELS = 25_000_000
MIN_AVATAR_SIDE = 64
AVATAR_SIZE = 256
AVATAR_FORMATS = {"PNG", "JPEG", "WEBP"}


def render_avatar(data: bytes) -> bytes:
    """Decode a customer-supplied picture into a square PNG, or explain why not."""
    if not data:
        raise ValueError("请选择头像图片")
    if len(data) > MAX_AVATAR_UPLOAD:
        raise ValueError("头像不能超过 4 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in AVATAR_FORMATS:
                    raise ValueError("头像仅支持 PNG、JPEG、WebP")
                if source.width * source.height > MAX_AVATAR_PIXELS:
                    raise ValueError("头像像素过多，请换一张更小的图片")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("头像不支持动态图片")
                if min(source.size) < MIN_AVATAR_SIDE:
                    raise ValueError(f"头像短边至少 {MIN_AVATAR_SIDE} 像素")
                # exif_transpose honours the orientation tag, then everything the
                # camera wrote is dropped when the image is re-encoded below.
                image = ImageOps.exif_transpose(source).convert("RGB")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("头像文件损坏或格式不支持") from exc
    side = min(image.size)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    square = image.crop((left, top, left + side, top + side))
    square = square.resize((AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    square.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


class AvatarStore:
    """Avatar files live beside the account database; every write is atomic."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def save(self, data: bytes) -> str:
        """Write one picture under a fresh key and return that key."""
        key = uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".avatar.", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, self.root / f"{key}.png")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return key

    def path(self, key: str) -> Path | None:
        """Resolve one key to its file; anything else, including traversal, is None."""
        if not KEY_PATTERN.match(key or ""):
            return None
        path = self.root / f"{key}.png"
        return path if path.is_file() else None

    def remove(self, key: str) -> None:
        path = self.path(key)
        if path:
            path.unlink(missing_ok=True)

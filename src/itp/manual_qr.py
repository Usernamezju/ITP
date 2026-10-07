"""The operator's own WeChat/Alipay collection codes, kept as files.

A manual payment sends the payer to a picture the shop already owns; the
platform never touches the money and never learns whether it arrived.  The
pictures therefore live outside Git and outside ``.env``: each upload is
decoded and re-encoded, stored under a random key in
``data/payment/manual/``, and only that key is written to the settings file.
"""

import io
import os
import tempfile
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from itp.config import MANUAL_QR_KEY

MAX_QR_UPLOAD = 4 * 1024 * 1024
MAX_QR_PIXELS = 16_000_000
MIN_QR_SIDE = 120
QR_FORMATS = {"PNG", "JPEG", "WEBP"}


def render_qr(data: bytes) -> bytes:
    """Decode an uploaded collection code into a clean PNG, or explain why not."""
    if not data:
        raise ValueError("请选择收款码图片")
    if len(data) > MAX_QR_UPLOAD:
        raise ValueError("收款码图片不能超过 4 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in QR_FORMATS:
                    raise ValueError("收款码仅支持 PNG、JPEG、WebP")
                if source.width * source.height > MAX_QR_PIXELS:
                    raise ValueError("收款码像素过多，请换一张更小的图片")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("收款码不支持动态图片")
                if min(source.size) < MIN_QR_SIDE:
                    raise ValueError(f"收款码短边至少 {MIN_QR_SIDE} 像素")
                image = source.convert("RGB")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("收款码文件损坏或格式不支持") from exc
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


class ManualQrStore:
    """One directory of collection codes; every write replaces atomically."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def save(self, data: bytes) -> str:
        """Write one picture under a fresh key and return that file name."""
        name = uuid4().hex + ".png"
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".qr.", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, self.root / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return name

    def path(self, name: str) -> Path | None:
        """Resolve one key to its file; anything else, traversal included, is None."""
        if not MANUAL_QR_KEY.match(name or ""):
            return None
        path = self.root / name
        return path if path.is_file() else None

    def remove(self, name: str) -> None:
        path = self.path(name)
        if path:
            path.unlink(missing_ok=True)

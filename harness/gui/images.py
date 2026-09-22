"""Image helpers for the GUI. Qt is optional here.

``encode_image_file`` downscales with QImage when PySide6 is available
(keeps the Ollama payload small) and falls back to raw bytes otherwise,
so the pure logic stays testable without Qt.
"""

from __future__ import annotations

import base64
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

#: Largest single image we'll read (bytes); protects the chat payload.
MAX_IMAGE_BYTES = 12 * 1024 * 1024

#: Longest side after downscaling for the Ollama ``images`` field.
OLLAMA_MAX_DIM = 1568


def is_image_file(path: str | Path) -> bool:
    """True for file extensions we accept as attachable images."""
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def encode_image_file(path: str | Path, max_dim: int = OLLAMA_MAX_DIM) -> str:
    """Read *path* and return a base64 image payload for Ollama.

    Downscales to ``max_dim`` on the longest side and re-encodes as
    JPEG (quality 85) when Qt is available; otherwise the raw file
    bytes are encoded. Raises ``ValueError`` for missing files,
    non-images, or oversized files.
    """
    path = Path(path)
    if not is_image_file(path):
        raise ValueError(f"Not a supported image file: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Cannot read image: {exc}") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image is {len(raw) / 1024 / 1024:.1f} MB "
            f"(limit {MAX_IMAGE_BYTES / 1024 / 1024:.0f} MB)"
        )
    try:
        from PySide6.QtCore import QBuffer, QIODevice, Qt
        from PySide6.QtGui import QImage
    except ImportError:
        return base64.b64encode(raw).decode("ascii")

    image = QImage()
    if image.loadFromData(raw) and not image.isNull():
        longest = max(image.width(), image.height())
        if longest > max_dim:
            image = image.scaled(
                max_dim,
                max_dim,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "JPEG", 85)
        return base64.b64encode(bytes(buffer.data())).decode("ascii")
    # Qt couldn't decode it — send the raw bytes and let Ollama decide.
    return base64.b64encode(raw).decode("ascii")

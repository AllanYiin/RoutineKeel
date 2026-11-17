import io
import json
import warnings
from pathlib import PurePosixPath

from PIL import Image, UnidentifiedImageError

from ..contracts import StudioError
from .mermaid import parse_mermaid
from .models import ImportLimits, Preview
from .text import decode_text

TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".tsv", ".log", ".py", ".yaml", ".yml"}
IMAGE_SUFFIXES = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


def fit_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    if min(width, height, max_width, max_height) <= 0:
        raise StudioError("INPUT_INVALID", "預覽尺寸必須大於零。")
    scale = min(max_width / width, max_height / height, 1)
    return max(1, round(width * scale)), max(1, round(height * scale))


def preview_file(name: str, data: bytes, limits: ImportLimits) -> Preview:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in TEXT_SUFFIXES | {".mmd", ".mermaid"}:
        text = decode_text(data)
        kind = "mermaid" if suffix in {".mmd", ".mermaid"} else "text"
        if kind == "mermaid":
            parse_mermaid(text)
        if suffix == ".json":
            try:
                json.loads(text)
            except ValueError as exc:
                raise StudioError("INPUT_INVALID", "JSON 來源格式不完整。") from exc
        return Preview(
            kind, text[: limits.preview_chars], truncated=len(text) > limits.preview_chars
        )
    if suffix in IMAGE_SUFFIXES:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if (
                        image.format != IMAGE_SUFFIXES[suffix]
                        or image.width * image.height > limits.max_image_pixels
                    ):
                        raise StudioError(
                            "SOURCE_PREVIEW_UNAVAILABLE", "圖片格式不符或像素數超過限制。"
                        )
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    return Preview(
                        "image",
                        media_type=Image.MIME[image.format],
                        width=image.width,
                        height=image.height,
                    )
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise StudioError(
                "SOURCE_PREVIEW_UNAVAILABLE", "圖片無法安全解碼，請提供完整 PNG／JPEG／WebP。"
            ) from exc
    raise StudioError("SOURCE_PREVIEW_UNAVAILABLE", f"尚不支援 {suffix or '無副檔名'} 來源預覽。")

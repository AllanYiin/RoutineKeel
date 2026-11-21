"""Deterministic, full-resolution image disclosure for ordinary Discovery."""

import base64
import hashlib
import io
import json
import math

from PIL import Image, ImageOps

from ..contracts import StudioError
from ..project.models import canonical_json

TEXT_LIMIT = 256_000
REQUEST_LIMIT = 32 * 1024 * 1024
DEFAULT_IMAGE_INPUT_TOKENS = 65_536


def image_part(data):
    with Image.open(io.BytesIO(data)) as source:
        if getattr(source, "n_frames", 1) != 1:
            raise StudioError(
                "IR_UNSUPPORTED_FEATURE", "流程圖片須為單張靜態圖；動畫不能只取第一格當完整來源。"
            )
        oriented = ImageOps.exif_transpose(source)
        mode = oriented.mode
        if (
            mode not in {"1", "L", "LA", "RGB", "RGBA", "I", "I;16"}
            or "transparency" in oriented.info
        ):
            mode = (
                "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
            )
        pixels = oriented.convert(mode)
        clean = Image.new(mode, pixels.size)
        clean.paste(pixels)
        buffer = io.BytesIO()
        clean.save(buffer, format="PNG")
        payload = buffer.getvalue()
    if len(payload) * 4 // 3 > REQUEST_LIMIT:
        raise StudioError(
            "INPUT_INVALID", "完整圖片超過單次分析大小限制；請提供較小來源，不會自動縮圖或裁切。"
        )
    return (
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(payload).decode()},
        },
        {
            "width": clean.width,
            "height": clean.height,
            "media_type": "image/png",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "transform": "orientation_and_metadata_only",
        },
    )


def context_from_messages(messages):
    content = messages[1]["content"]
    return json.loads(content if isinstance(content, str) else content[0]["text"])


def text_bytes(messages):
    without_images = []
    for message in messages:
        content = message["content"]
        if isinstance(content, list):
            content = [part for part in content if part["type"] == "text"]
        without_images.append({**message, "content": content})
    return len(canonical_json({"messages": without_images}).encode())


def check_message_size(messages):
    if (
        text_bytes(messages) > TEXT_LIMIT
        or len(canonical_json({"messages": messages}).encode()) > REQUEST_LIMIT
    ):
        raise StudioError("INPUT_INVALID", "本次文字或圖片內容過大，請縮小選取來源範圍。")


def input_reservation(messages, tokens_per_image):
    images = sum(
        part["type"] == "image_url"
        for message in messages
        if isinstance(message["content"], list)
        for part in message["content"]
    )
    return text_bytes(messages) + images * tokens_per_image + 1024


def validate_image_locator(locator, path):
    if locator == path:
        return
    prefix = path + ":region="
    if not locator.startswith(prefix):
        raise StudioError(
            "EVIDENCE_INSUFFICIENT",
            "圖片證據請引用檔名或正規化 region=x,y,width,height；不能假裝有文字行號。",
        )
    try:
        x, y, width, height = map(float, locator[len(prefix) :].split(","))
    except ValueError as exc:
        raise StudioError("EVIDENCE_INSUFFICIENT", "圖片區域格式不正確。") from exc
    if not all(math.isfinite(v) for v in (x, y, width, height)) or not (
        0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 - x and 0 < height <= 1 - y
    ):
        raise StudioError("EVIDENCE_INSUFFICIENT", "圖片證據區域超出校正方向後的完整圖片。")

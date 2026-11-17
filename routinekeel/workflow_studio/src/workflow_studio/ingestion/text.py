from ..contracts import StudioError


def decode_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StudioError("SOURCE_PREVIEW_UNAVAILABLE", "請將文字來源轉為 UTF-8。") from exc
    if not text.strip() or "\x00" in text:
        raise StudioError("INPUT_INVALID", "來源文字空白或含有二進位資料。")
    return text

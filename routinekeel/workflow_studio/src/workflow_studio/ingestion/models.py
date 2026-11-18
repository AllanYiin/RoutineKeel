import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ImportLimits:
    max_files: int = 1000
    max_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 50 * 1024 * 1024
    max_image_pixels: int = 20_000_000
    max_zip_ratio: int = 100
    preview_chars: int = 8000


@dataclass(frozen=True)
class Preview:
    kind: str
    text: str = ""
    media_type: str = "text/plain"
    width: int | None = None
    height: int | None = None
    truncated: bool = False


@dataclass(frozen=True)
class Source:
    source_id: str
    project_id: str
    version: int
    name: str
    kind: str
    content_hash: str
    manifest_json: str
    status: str
    created_at: str

    @property
    def manifest(self) -> dict:
        return json.loads(self.manifest_json)

"""Stable, non-sensitive errors shared by Studio interfaces."""


class StudioError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_response(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": str(self)}}

from urllib.parse import urlsplit

from pydantic import Field, field_validator

from ..contracts import StudioError
from ..ir.models import Contract


class RuntimeLLMSettings(Contract):
    endpoint: str
    prompt: str = Field(min_length=1, max_length=8000)
    output_fields: dict[str, str]
    input_usd_per_million: float = Field(gt=0)
    output_usd_per_million: float = Field(gt=0)
    pricing_note: str = Field(min_length=1)

    @field_validator("endpoint")
    @classmethod
    def https_endpoint(cls, value):
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("HTTPS endpoint without credentials/query required")
        return value

    @field_validator("output_fields")
    @classmethod
    def structured_output(cls, value):
        if (
            not value
            or len(value) > 30
            or not set(value.values()) <= {"string", "integer", "number", "boolean"}
        ):
            raise ValueError("Output fields require supported scalar types")
        return value


def validate_llm(node):
    from pydantic import ValidationError

    try:
        settings = RuntimeLLMSettings.model_validate(node.parameters)
    except ValidationError as exc:
        raise StudioError(
            "CONTRACT_MISMATCH", "LLM 端點、費率、提示或結構化輸出契約不完整。"
        ) from exc
    if (
        node.cache_policy.mode != "none"
        or node.llm_policy.max_output_tokens < 64
        or node.llm_policy.timeout_seconds < 5
        or node.llm_policy.budget_micro_usd > 1_000_000_000
    ):
        raise StudioError(
            "CONTRACT_MISMATCH",
            "LLM 須無快取、輸出上限至少 64 tokens、逾時至少 5 秒且預算不超過 Runtime 上限。",
        )
    return settings

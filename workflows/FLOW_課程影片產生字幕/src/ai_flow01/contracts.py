from __future__ import annotations

from typing import Literal

from flow_base.contracts import Contract, Step
from pydantic import Field, model_validator


class LocalReviewPolicy(Contract):
    enabled: Literal[False] = False
    max_calls: Literal[0] = 0
    max_segments: int = Field(default=20, ge=0, le=20)
    max_total_chars: int = Field(default=3000, ge=0, le=3000)
    context_chars: int = Field(default=60, ge=0, le=60)

class Options(Contract):
    language: str | None = "zh"
    traditional_chinese: bool = True
    device: Literal["cpu", "cuda", "auto"] = "auto"
    allow_cpu_fallback: bool = False
    audio_preprocessing: Literal["standard", "reference"] = "standard"
    beam_size: int = Field(default=5, ge=1, le=10)
    vad_threshold: float = Field(default=0.3, gt=0, lt=1)
    vad_min_speech_ms: int = Field(default=250, ge=0, le=5000)
    vad_min_silence_ms: int = Field(default=2200, ge=100, le=5000)
    vad_skip_silence_seconds: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    vad_speech_pad_ms: int = Field(default=250, ge=0, le=2000)
    vad_max_speech_seconds: float = Field(default=28, gt=0, le=30)
    max_line_chars: int = Field(default=30, ge=8, le=80)
    max_cue_seconds: float = Field(default=12, gt=0.5, le=20)
    glossary: list[str] = Field(default_factory=list, max_length=100)
    initial_prompt: str = Field(default='繁體中文', max_length=300)
    initial_prompt_enabled: bool = True
    max_cps: float = Field(default=15, gt=0)
    min_cue_seconds: float = Field(default=0.5, gt=0)
    low_logprob_threshold: float = -0.8
    spot_check_count: int = Field(default=10, ge=0, le=100)
    min_free_bytes: int = Field(default=536870912, ge=0)
    max_audio_seconds: int = Field(default=21600, ge=1, le=86400)
    worker_timeout_seconds: int = Field(default=21600, ge=1, le=172800)
    llm: LocalReviewPolicy = Field(default_factory=LocalReviewPolicy)

    @model_validator(mode='after')
    def local_only(self):
        self.initial_prompt = (self.initial_prompt.strip() or '繁體中文') if self.initial_prompt_enabled else ''
        if self.llm.enabled:
            raise ValueError('LOCAL_LLM_FORBIDDEN: 本字幕 runner 禁止 LLM API 呼叫；請明確匯出有限疑義片段供外部檢閱。')
        return self


class Workflow(Contract):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    runtime: str = "0.2"
    profile: str = Field(default="auto", pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    execution_selection: dict = Field(default_factory=dict)
    profile_project: str | None = None
    check_imports: list[str] = Field(default_factory=list, max_length=30)
    model: Literal["whisper-tiny", "whisper-small", "whisper-large-v3"] = "whisper-large-v3"
    steps: list[Step] = Field(min_length=1)
    options: Options = Field(default_factory=Options)

    @model_validator(mode="after")
    def validate_graph(self):
        if self.runtime != "0.2":
            raise ValueError("this runtime supports workflow API 0.2 only")
        if self.profile not in {"auto", "core", "asr-cpu", "asr-cuda"} and not self.profile_project:
            raise ValueError("custom profiles require profile_project pointing to a locked uv project")
        if self.profile in {"auto", "core", "asr-cpu", "asr-cuda"} and self.profile_project:
            raise ValueError("built-in profiles cannot be overridden")
        import re
        if any(not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", x) for x in self.check_imports):
            raise ValueError("check_imports must contain module names")
        seen = set()
        for step in self.steps:
            if step.id in seen or not set(step.needs) <= seen:
                raise ValueError("steps must have unique IDs in dependency order (no cycles)")
            seen.add(step.id)
        handlers = [s.handler for s in self.steps]
        subtitle = ["media.extract", "speech.vad", "speech.transcribe", "text.review", "subtitle.export"]
        if any(h in subtitle for h in handlers):
            if handlers != subtitle:
                raise ValueError("built-in subtitle handlers require the complete ordered pipeline")
            for index, step in enumerate(self.steps):
                expected = [] if index == 0 else [self.steps[index - 1].id]
                if step.needs != expected:
                    raise ValueError("subtitle dependencies must match the declared sequential pipeline")
        if handlers == subtitle and self.profile == "core":
            raise ValueError("subtitles require an ASR profile")
        if self.options.device == "cuda" and self.profile not in {"auto", "asr-cuda"}:
            raise ValueError("device=cuda requires asr-cuda profile")
        return self


class Word(Contract):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    word: str


class Segment(Contract):
    id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str
    words: list[Word] = Field(default_factory=list)

    @model_validator(mode="after")
    def times(self):
        if self.end <= self.start:
            raise ValueError("segment end must exceed start")
        last = self.start - 0.1
        for w in self.words:
            if w.end < w.start or w.start < last or w.start < self.start - 0.1 or w.end > self.end + 0.1:
                raise ValueError("invalid word timeline")
            last = w.start
        return self


def subtitle_workflow() -> Workflow:
    return Workflow(id="lecture-subtitles", steps=[
        Step(id="extract", handler="media.extract"),
        Step(id="vad", handler="speech.vad", needs=["extract"]),
        Step(id="transcribe", handler="speech.transcribe", needs=["vad"]),
        Step(id="review", handler="text.review", needs=["transcribe"]),
        Step(id="export", handler="subtitle.export", needs=["review"]),
    ])

"""Run pinned speech dependencies in their existing Python environment."""

import json
import sys
import wave
from importlib.metadata import version


def samples(path):
    import numpy as np

    with wave.open(path, "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError("expected 16 kHz mono PCM16")
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768


def detect(request):
    if version("faster-whisper") != "1.2.0":
        raise RuntimeError("unsupported faster-whisper version")
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = samples(request["audio"])
    return get_speech_timestamps(audio, VadOptions(**request["settings"])) if audio.size else []


def transcribe(request):
    if version("openai-whisper") != "20231117":
        raise RuntimeError("unsupported OpenAI Whisper version")
    import torch
    import whisper

    torch.set_num_threads(min(4, torch.get_num_threads()))
    model = whisper.load_model(request["model"], device="cpu")
    results = []
    for clip in request["clips"]:
        result = model.transcribe(samples(clip), task="transcribe", fp16=False, temperature=0,
                                  word_timestamps=True, condition_on_previous_text=False,
                                  initial_prompt=request["prompt"] or None)
        fields = ("start", "end", "text", "words")
        results.append({"language": result["language"], "segments": [
            {key: segment[key] for key in fields} for segment in result["segments"]
        ]})
    return results


if __name__ == "__main__":
    handlers = {"vad": detect, "transcribe": transcribe}
    output = handlers[sys.argv[1]](json.load(sys.stdin))
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False, allow_nan=False).encode())

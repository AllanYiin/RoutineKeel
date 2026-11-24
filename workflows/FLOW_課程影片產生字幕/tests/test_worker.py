import sys
import types

import pytest
from ai_flow01 import worker


def test_gpu_probe_failure_can_fall_back(monkeypatch):
    def failed_probe():
        raise RuntimeError("driver unavailable")
    monkeypatch.setattr(worker, "configure_cuda_libraries", lambda: None)
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=failed_probe))
    calls = []
    def model(path, **kwargs):
        calls.append(kwargs)
        return object()
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=model))
    _, device, fallback = worker.load_model({"device": "auto", "model_path": "fixture", "allow_cpu_fallback": True})
    assert device == "cpu" and fallback == "RuntimeError" and calls[0]["compute_type"] == "int8"
    with pytest.raises(RuntimeError):
        worker.load_model({"device": "auto", "model_path": "fixture", "allow_cpu_fallback": False})


def test_gpu_model_failure_can_fall_back(monkeypatch):
    monkeypatch.setattr(worker, "configure_cuda_libraries", lambda: None)
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    def model(path, **kwargs):
        if kwargs["device"] == "cuda":
            raise RuntimeError("missing cuDNN")
        return object()
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=model))
    _, device, _ = worker.load_model({"device": "auto", "model_path": "fixture", "allow_cpu_fallback": True})
    assert device == "cpu"


def test_speech_regions_keep_long_silence_and_bound_chunks():
    regions = [{'start': 16000, 'end': 32000}, {'start': 40000, 'end': 64000},
               {'start': 160000, 'end': 192000}, {'start': 192000, 'end': 700000}]
    assert worker.merge_speech_regions(regions) == [
        {'start': 16000, 'end': 64000}, {'start': 160000, 'end': 192000}, {'start': 192000, 'end': 700000}]



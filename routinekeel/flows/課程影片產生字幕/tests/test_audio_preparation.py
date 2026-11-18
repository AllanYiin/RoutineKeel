import io
import wave

import numpy as np
import pytest

from ai_flow01 import audio_preparation as prep, worker
from ai_flow01.contracts import Options


def test_readability_padding_stays_out_of_skipped_silence():
    from ai_flow01.portable_core import ease_short_cues
    gaps = [{"start": 0, "end": 4.0}, {"start": 5.0, "end": 9.0}]
    cues = [{"start": 4.9, "end": 5.0, "text": "a few words"}]
    result = ease_short_cues(cues, {"min_cue_seconds": .5, "max_cps": 15}, 9.0, gaps)
    assert 4.0 <= result[0]["start"] < 4.9
    assert result[0]["end"] == 5.0


def test_unconditional_silence_skip_uses_original_sample_coordinates():
    rate, pad = 16000, 4000
    speech = [{"start": 4*rate, "end": 5*rate}, {"start": 8*rate, "end": 9*rate},
              {"start": 12*rate+1, "end": 13*rate}]
    regions, skipped = worker.pad_speech_regions(speech, 17*rate, pad, 3*rate)
    assert [(r['start_sample'], r['end_sample']) for r in skipped] == [
        (0, 4*rate), (9*rate, 12*rate+1), (13*rate, 17*rate)]
    assert regions == [{"start": 4*rate, "end": 5*rate+pad}, {"start": 8*rate-pad, "end": 9*rate},
                       {"start": 12*rate+1, "end": 13*rate}]
    assert worker.pad_speech_regions([], 4*rate, pad, 3*rate)[1][0]["end"] == 4
    assert worker.pad_speech_regions([], 3*rate, pad, 3*rate) == ([], [])
    for gap in skipped:
        assert all(r["end"] <= gap["start_sample"] or r["start"] >= gap["end_sample"] for r in regions)


def test_defaults_reach_silero_and_extracted_region_audio(tmp_path, monkeypatch):
    import faster_whisper.vad as vad_module
    calls = []
    def silero(samples, options):
        calls.append(options)
        return [{"start": 64000, "end": 80000}]
    monkeypatch.setattr(vad_module, "get_speech_timestamps", silero)
    source = tmp_path / "vocals.wav"
    signal = np.arange(144000, dtype=np.int16)
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(signal.tobytes())
    target = tmp_path / "regions.json"
    opts = Options().model_dump()
    worker.vad({"input": str(source), "output": str(target), **opts})
    assert (calls[0].threshold, calls[0].min_speech_duration_ms, calls[0].min_silence_duration_ms) == (.3,250,2200)
    import json
    plan = json.loads(target.read_text(encoding="utf-8"))
    assert plan["parameters"]["skip_silence_seconds"] == 3
    assert [r["start"] for r in plan["skipped_silence"]] == [0,5]
    assert np.array_equal(worker.read_pcm(tmp_path / plan["regions"][0]["audio_file"]), signal[64000:80000])


@pytest.mark.parametrize("length", [1, 37, 40, 41, 75, 76, 77, 150])
def test_chunk_crossfade_preserves_sample_positions(monkeypatch, length):
    monkeypatch.setattr(prep, "WINDOW", 40)
    monkeypatch.setattr(prep, "OVERLAP", 4)
    monkeypatch.setattr(prep, "separate_window", lambda pcm, _: pcm[0].astype(np.float32))
    signal = np.tile(np.arange(length, dtype=np.int16), (2,1))
    chunks = ((signal[:, i:i+7], None) for i in range(0, length, 7))
    assert np.allclose(np.concatenate(list(prep.vocal_blocks(chunks, None))), signal[0])


@pytest.mark.parametrize("silent", [False, True])
def test_normalization_preserves_silence_channel_duration_and_target(tmp_path, silent):
    x = np.zeros(32000, dtype="<f4")
    if not silent:
        x[8000:24000] = .02
    dest = tmp_path / "out.wav"
    levels = prep.normalized_wave(io.BytesIO(x.tobytes()), dest, len(x))
    out = worker.read_pcm(dest) / 32768
    assert len(out) == len(x) and np.all(out[x == 0] == 0)
    assert levels["output_peak"] <= 10 ** (-1 / 20)
    if not silent:
        assert np.allclose(out[x != 0], 0.1, atol=1/32768)

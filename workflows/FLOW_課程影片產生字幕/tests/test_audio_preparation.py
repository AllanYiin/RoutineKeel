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
    monkeypatch.setattr(prep, "separate_window", lambda pcm, _, mode="standard": pcm[0].astype(np.float32))
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


def test_denoise_uses_requested_parameters_and_preserves_short_audio(monkeypatch):
    torch = pytest.importorskip("torch")
    nr = pytest.importorskip("noisereduce")
    calls = []

    def reduce_noise(**kwargs):
        calls.append(kwargs)
        return kwargs["y"].copy()

    monkeypatch.setattr(nr, "reduce_noise", reduce_noise)
    audio = torch.full((2, 37), 0.01)
    actual = prep.denoise(audio)
    assert torch.equal(actual, audio)
    assert calls[0]["y"].shape == (2, 1024)
    assert {k: v for k, v in calls[0].items() if k != "y"} == {
        "sr": 44100, "stationary": False, "prop_decrease": 0.75,
        "n_fft": 1024, "win_length": 1024, "hop_length": 256, "use_torch": False,
    }


@pytest.mark.parametrize("length", [1, 37, 2048, 16000])
def test_real_nonstationary_denoise_preserves_finite_sample_timeline(length):
    torch = pytest.importorskip("torch")
    pytest.importorskip("noisereduce")
    x = np.random.default_rng(7).normal(0, 0.01, (2, length)).astype(np.float32)
    output = prep.denoise(torch.from_numpy(x))
    assert output.shape == (2, length)
    assert torch.isfinite(output).all()


def test_denoise_error_does_not_silently_use_unprocessed_audio(monkeypatch):
    torch = pytest.importorskip("torch")
    nr = pytest.importorskip("noisereduce")

    def fail(**kwargs):
        raise RuntimeError("denoiser failed")

    monkeypatch.setattr(nr, "reduce_noise", fail)
    with pytest.raises(RuntimeError, match="denoiser failed"):
        prep.denoise(torch.ones((2, 1024)))


def test_nonstationary_denoise_keeps_an_impulse_at_its_sample_position():
    torch = pytest.importorskip("torch")
    pytest.importorskip("noisereduce")
    audio = np.zeros((2, 44100), dtype=np.float32)
    audio[:, 22050] = 0.5
    output = prep.denoise(torch.from_numpy(audio)).numpy()
    assert np.all(np.argmax(np.abs(output), axis=1) == 22050)


def test_preemphasis_keeps_filter_state_across_arbitrary_pcm_blocks():
    audio = np.random.default_rng(3).normal(0, 0.02, 33001).astype(np.float32)
    whole, expected_last = prep.preemphasize(audio)
    result, previous = [], 0.0
    for block in np.array_split(audio, 73):
        output, previous = prep.preemphasize(block, previous)
        result.append(output)
    assert np.allclose(np.concatenate(result), whole, rtol=0, atol=1e-8)
    assert previous == expected_last == float(audio[-1])
    silent, _ = prep.preemphasize(np.zeros(16000, dtype=np.float32))
    assert np.count_nonzero(silent) == 0


def test_preemphasis_boosts_high_frequency_relative_to_low_frequency():
    t = np.arange(16000, dtype=np.float32) / 16000
    gain = []
    for hz in (200, 4000):
        x = 0.01 * np.sin(2 * np.pi * hz * t)
        y, _ = prep.preemphasize(x)
        gain.append(float(np.sqrt(np.mean(y[100:] ** 2) / np.mean(x[100:] ** 2))))
    assert gain[1] > 10 * gain[0]


def test_enhanced_pcm_stays_below_peak_ceiling_and_preserves_duration(tmp_path):
    audio = np.tile(np.array([-0.9, 0.9], dtype=np.float32), 16000)
    emphasized, _ = prep.preemphasize(audio)
    assert np.max(np.abs(emphasized)) > 1
    target = tmp_path / "enhanced.wav"
    levels = prep.normalized_wave(io.BytesIO(emphasized.astype("<f4").tobytes()), target, len(audio))
    output = worker.read_pcm(target) / 32768
    assert len(output) == len(audio)
    assert np.max(np.abs(output)) <= 10 ** (-1 / 20) + 1 / 32768
    assert levels["input_peak_dbfs"] > 0


def test_changed_preprocessing_has_its_own_cache_settings():
    from ai_flow01.adapter import step_settings
    from ai_flow01.contracts import subtitle_workflow

    settings = step_settings(subtitle_workflow(), "media.extract", {})
    assert settings["preprocessing"]["mode"] == "standard"
    workflow = subtitle_workflow()
    workflow.options.audio_preprocessing = "reference"
    reference = step_settings(workflow, "media.extract", {})
    assert reference["preprocessing"]["revision"] == 2
    assert reference["preprocessing"]["noise_reduction"]["prop_decrease"] == 0.75
    assert reference["preprocessing"]["enhancement"]["pre_emphasis_coefficient"] == 0.97
    assert reference != settings
    updated = step_settings(subtitle_workflow(), "media.extract", {"separator": {"versions": {"noisereduce": "new-test-version"}}})
    assert updated != settings
    assert updated["separator_backend"]["versions"]["noisereduce"] == "new-test-version"


def test_reference_mode_requires_an_explicit_valid_option():
    assert Options().audio_preprocessing == "standard"
    assert Options(audio_preprocessing="reference").audio_preprocessing == "reference"
    with pytest.raises(ValueError):
        Options(audio_preprocessing="unknown")

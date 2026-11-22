"""PTS-aligned decoding and local Hybrid Demucs preprocessing for the ASR worker."""
from __future__ import annotations

import math
import os
import tempfile
import wave
from pathlib import Path

MODEL_RATE = 44_100
ASR_RATE = 16_000
WINDOW = 10 * MODEL_RATE
OVERLAP = MODEL_RATE
TARGET_DBFS = -20.0
PEAK_DBFS = -1.0
PREEMPHASIS_COEFFICIENT = 0.97


def preprocessing_spec(mode="standard"):
    """Versioned settings also participate in extraction's cache fingerprint."""
    if mode not in {"standard", "reference"}:
        raise ValueError("AUDIO_PREPROCESSING_INVALID: unknown mode")
    if mode == "standard":
        return {"revision": 1, "mode": mode,
                "order": ["spectral_denoise", "demucs_vocals", "volume_normalize"],
                "noise_reduction": {"library": "torch_stft", "n_fft": 1024, "hop_length": 256,
                                    "noise_quantile": 0.2, "minimum_gain_db": -12.0, "sample_rate": MODEL_RATE},
                "enhancement": {"pre_emphasis_coefficient": 0.0, "target_active_rms_dbfs": TARGET_DBFS,
                                "peak_ceiling_dbfs": PEAK_DBFS, "max_gain_db": 20.0}}
    return {
        "revision": 2, "mode": mode,
        "order": ["noisereduce_nonstationary", "demucs_vocals", "pre_emphasis", "volume_normalize"],
        "noise_reduction": {"library": "noisereduce", "stationary": False,
                            "prop_decrease": 0.75, "n_fft": 1024,
                            "win_length": 1024, "hop_length": 256, "use_torch": False,
                            "sample_rate": MODEL_RATE},
        "enhancement": {"pre_emphasis_coefficient": PREEMPHASIS_COEFFICIENT,
                        "pre_emphasis_initial_previous_sample": 0.0,
                        "target_active_rms_dbfs": TARGET_DBFS, "peak_ceiling_dbfs": PEAK_DBFS,
                        "max_gain_db": 20.0},
    }


def standard_denoise(mix):
    """Retain the 0.4.1 default until reference preprocessing has accuracy evidence."""
    import torch

    window = torch.hann_window(1024, device=mix.device)
    spectrum = torch.stft(mix, 1024, hop_length=256, window=window,
                          pad_mode="constant", return_complex=True)
    power = spectrum.abs().square().mean(dim=0)
    noise = torch.quantile(power, 0.2, dim=-1, keepdim=True)
    gain = (1 - 2 * noise / power.clamp_min(1e-12)).clamp(min=10 ** (-12 / 20), max=1)
    return torch.istft(spectrum * gain, 1024, hop_length=256, window=window, length=mix.shape[-1])


def pcm_blocks(source, rate, layout, max_seconds):
    """Yield planar PCM16 on the original timeline, including gaps in packet PTS."""
    import av
    import numpy as np

    written = 0
    channels = 1 if layout == "mono" else 2
    with av.open(str(source)) as container:
        if not container.streams.audio:
            raise ValueError("NO_AUDIO_STREAM: input contains no audio")
        stream = container.streams.audio[0]
        origin = (container.start_time or 0) / 1_000_000
        total = container.duration / 1_000_000 if container.duration else None
        resampler = av.AudioResampler(format="s16p", layout=layout, rate=rate)

        def frames():
            for frame in container.decode(stream):
                yield from resampler.resample(frame)
            yield from resampler.resample(None)

        for frame in frames():
            pos = written if frame.pts is None else max(0, round((float(frame.pts * frame.time_base) - origin) * rate))
            if pos + frame.samples > max_seconds * rate:
                raise ValueError("AUDIO_LIMIT: maximum audio duration exceeded")
            while written < pos:
                count = min(rate, pos - written)
                yield np.zeros((channels, count), dtype=np.int16), total
                written += count
            skip = min(frame.samples, written - pos)
            if skip < frame.samples:
                yield frame.to_ndarray()[:, skip:], total
                written += frame.samples - skip
    if not written:
        raise ValueError("EMPTY_AUDIO: no decoded samples")


def denoise(mix):
    """Nonstationary spectral gating using the user's noisereduce parameters."""
    import noisereduce as nr
    import numpy as np
    import torch

    values = mix.detach().cpu().numpy()
    if not np.isfinite(values).all():
        raise ValueError("NOISE_REDUCTION_INVALID: non-finite input")
    length = values.shape[-1]
    if not length or not values.any():
        return mix.clone()
    # STFT needs at least n_fft samples; never change the decoded timeline.
    padded = np.pad(values, ((0, 0), (0, max(0, 1024 - length))))
    clean = np.asarray(nr.reduce_noise(y=padded, sr=MODEL_RATE, stationary=False,
                                     prop_decrease=0.75, n_fft=1024, win_length=1024,
                                     hop_length=256, use_torch=False), dtype=np.float32)
    if clean.shape != padded.shape or not np.isfinite(clean).all():
        raise ValueError("NOISE_REDUCTION_INVALID: invalid output samples")
    return torch.from_numpy(np.ascontiguousarray(clean[:, :length])).to(mix.device)


def preemphasize(block, previous=0.0):
    """Continuous first-order high-pass filter; carry state across PCM blocks."""
    import numpy as np

    values = np.asarray(block, dtype=np.float32).reshape(-1)
    if not np.isfinite(values).all():
        raise ValueError("AUDIO_ENHANCEMENT_INVALID: non-finite samples")
    if not len(values):
        return values.copy(), previous
    enhanced = values.copy()
    enhanced[0] -= PREEMPHASIS_COEFFICIENT * previous
    enhanced[1:] -= PREEMPHASIS_COEFFICIENT * values[:-1]
    return enhanced, float(values[-1])


def load_separator(weights):
    import torch
    from torchaudio.models import hdemucs_high

    # The official CPU bundle has no need for the ASR process's CUDA libraries.
    torch.set_num_threads(min(torch.get_num_threads(), 6))
    model = hdemucs_high(sources=["drums", "bass", "other", "vocals"])
    model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
    return model.eval()


def verify_separator(req):
    import importlib.metadata

    import torch
    # Check the denoiser's actual non-Torch path before any long recording runs.
    probe = torch.sin(torch.arange(MODEL_RATE).float() / 11)[None, :].repeat(2, 1) * 0.01
    mode = req.get("audio_preprocessing", "standard")
    spec = preprocessing_spec(mode)
    clean = denoise(probe) if mode == "reference" else standard_denoise(probe)
    if clean.shape != probe.shape or not torch.isfinite(clean).all():
        raise ValueError("NOISE_REDUCTION_INVALID: dependency verification failed")
    model = load_separator(req["weights"])
    with torch.inference_mode():
        stems = model(torch.zeros(1, 2, MODEL_RATE))
        vocals = stems[0, model.sources.index("vocals")]
        if vocals.shape != (2, MODEL_RATE) or not torch.isfinite(vocals).all():
            raise ValueError("DEMUCS_OUTPUT_INVALID: vocals failed the inference check")
    return {"model": "HDEMUCS_HIGH_MUSDB_PLUS", "stem": "vocals", "device": "cpu", "verified": True,
            "noisereduce_version": importlib.metadata.version("noisereduce") if mode == "reference" else None,
            "versions": {name: importlib.metadata.version(name)
                         for name in (("numpy", "scipy", "noisereduce", "torch", "torchaudio")
                                      if mode == "reference" else ("numpy", "torch", "torchaudio"))},
            "preprocessing": spec}


def separate_window(pcm, model, mode="standard"):
    import numpy as np
    import torch
    import torch.nn.functional as F

    length = pcm.shape[1]
    if not pcm.any():
        return np.zeros(length, dtype=np.float32)
    with torch.inference_mode():
        mix = torch.from_numpy(pcm.astype(np.float32)) / 32768
        clean = denoise(mix) if mode == "reference" else standard_denoise(mix)
        mean = clean.mean()
        scale = clean.std(unbiased=False).clamp_min(1e-6)
        # Supply context to the model even for very short inputs and final windows.
        standardized = F.pad((clean - mean) / scale, (0, WINDOW - length))
        stems = model(standardized[None])
        vocal = stems[0, model.sources.index("vocals"), :, :length].mean(dim=0)
        return (vocal * scale + mean).numpy()


def vocal_blocks(blocks, model, mode="standard"):
    """Crossfade adjacent predictions and emit exactly one output per input sample."""
    import numpy as np

    pending = np.empty((2, WINDOW), dtype=np.int16)
    fade = np.arange(1, OVERLAP + 1, dtype=np.float32) / (OVERLAP + 1)
    size, tail = 0, None

    def blend(pcm):
        vocal = separate_window(pcm, model, mode)
        if tail is not None:
            vocal[:OVERLAP] = tail * (1 - fade) + vocal[:OVERLAP] * fade
        return vocal

    for chunk, _ in blocks:
        while chunk.shape[1]:
            count = min(WINDOW - size, chunk.shape[1])
            pending[:, size:size + count] = chunk[:, :count]
            size += count
            chunk = chunk[:, count:]
            if size == WINDOW:
                vocal = blend(pending)
                yield vocal[:-OVERLAP]
                tail = vocal[-OVERLAP:].copy()
                pending[:, :OVERLAP] = pending[:, -OVERLAP:]
                size = OVERLAP
    if tail is not None and size == OVERLAP:
        yield tail
    elif size:
        yield blend(pending[:, :size])


def normalized_wave(samples, dest, frames):
    """Apply a single gain measured over active vocals, with a peak ceiling."""
    import numpy as np

    def blocks():
        samples.seek(0)
        while chunk := samples.read(4 * ASR_RATE):
            yield np.frombuffer(chunk, dtype="<f4")

    peak, energy, count = 0.0, 0.0, 0
    for block in blocks():
        if not np.isfinite(block).all():
            raise ValueError("DEMUCS_OUTPUT_INVALID: non-finite samples")
        peak = max(peak, float(np.max(np.abs(block))))
        active = block[np.abs(block) >= 10 ** (-50 / 20)]
        energy += float(np.sum(active.astype(np.float64) ** 2))
        count += len(active)
    rms = math.sqrt(energy / count) if count else 0.0
    gain = min(10.0, 10 ** (TARGET_DBFS / 20) / rms) if rms else 1.0
    if peak:
        gain = min(gain, 10 ** (PEAK_DBFS / 20) / peak)
    with wave.open(str(dest), "wb") as out:
        out.setparams((1, 2, ASR_RATE, frames, "NONE", "not compressed"))
        for block in blocks():
            out.writeframesraw(np.rint(block * (gain * 32768)).clip(-32768, 32767).astype("<i2").tobytes())
    return {"target_active_rms_dbfs": TARGET_DBFS, "peak_ceiling_dbfs": PEAK_DBFS,
            "input_active_rms_dbfs": 20 * math.log10(rms) if rms else None,
            "input_peak_dbfs": 20 * math.log10(peak) if peak else None,
            "gain_db": 20 * math.log10(gain), "output_peak": peak * gain}


def prepare_audio(req):
    import json

    import av
    import numpy as np

    dest = Path(req["output"])
    mode = req.get("audio_preprocessing", "standard")
    spec = preprocessing_spec(mode)
    with wave.open(req["original"], "rb") as original:
        frames = original.getnframes()
        if original.getframerate() != ASR_RATE:
            raise ValueError("AUDIO_TIMELINE: reference sample rate mismatch")
    model = load_separator(req["weights"])
    source = pcm_blocks(req["input"], MODEL_RATE, "stereo", req["max_audio_seconds"])
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=ASR_RATE)
    completed = 0
    previous = 0.0
    # Bound working memory independently of the length of the lecture.
    with tempfile.TemporaryFile(dir=dest.parent) as samples:
        for block in vocal_blocks(source, model, mode):
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(block[None, :]), format="fltp", layout="mono")
            frame.sample_rate = MODEL_RATE
            for converted in resampler.resample(frame):
                enhanced = converted.to_ndarray()
                if mode == "reference":
                    enhanced, previous = preemphasize(enhanced, previous)
                samples.write(enhanced.astype("<f4", copy=False).tobytes())
            completed += len(block)
            print(json.dumps({"event": "progress", "completed": min(completed / MODEL_RATE, frames / ASR_RATE),
                              "total": frames / ASR_RATE, "unit": "秒音訊", "label": "去噪與 Demucs 人聲分離"}), flush=True)
        for converted in resampler.resample(None):
            enhanced = converted.to_ndarray()
            if mode == "reference":
                enhanced, previous = preemphasize(enhanced, previous)
            samples.write(enhanced.astype("<f4", copy=False).tobytes())
        actual = samples.tell() // 4
        if abs(actual - frames) > 16:
            raise ValueError(f"AUDIO_TIMELINE: Demucs produced {actual} samples, expected {frames}")
        samples.truncate(frames * 4)  # reconcile only the two resamplers' sub-ms end rounding
        temp = dest.with_suffix(".part")
        try:
            levels = normalized_wave(samples, temp, frames)
            os.replace(temp, dest)
        finally:
            temp.unlink(missing_ok=True)
    return {**spec,
            "separator": "torchaudio.HDEMUCS_HIGH_MUSDB_PLUS", "stem": "vocals", "device": "cpu",
            "sample_rate": ASR_RATE, "frames": frames, "normalization": levels}

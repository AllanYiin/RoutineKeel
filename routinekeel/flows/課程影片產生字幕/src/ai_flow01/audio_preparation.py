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
    """Apply one spectral gain to both channels, preserving stereo phase and length."""
    import torch

    fft_size = 1024
    window = torch.hann_window(fft_size, device=mix.device)
    spectrum = torch.stft(mix, fft_size, hop_length=256, window=window, pad_mode="constant", return_complex=True)
    power = spectrum.abs().square().mean(dim=0)
    noise = torch.quantile(power, 0.2, dim=-1, keepdim=True)
    gain = (1 - 2 * noise / power.clamp_min(1e-12)).clamp(min=10 ** (-12 / 20), max=1)
    return torch.istft(spectrum * gain, fft_size, hop_length=256, window=window, length=mix.shape[-1])


def load_separator(weights):
    import torch
    from torchaudio.models import hdemucs_high

    # The official CPU bundle has no need for the ASR process's CUDA libraries.
    torch.set_num_threads(min(torch.get_num_threads(), 6))
    model = hdemucs_high(sources=["drums", "bass", "other", "vocals"])
    model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
    return model.eval()


def verify_separator(req):
    import torch
    model = load_separator(req["weights"])
    with torch.inference_mode():
        stems = model(torch.zeros(1, 2, MODEL_RATE))
        vocals = stems[0, model.sources.index("vocals")]
        if vocals.shape != (2, MODEL_RATE) or not torch.isfinite(vocals).all():
            raise ValueError("DEMUCS_OUTPUT_INVALID: vocals failed the inference check")
    return {"model": "HDEMUCS_HIGH_MUSDB_PLUS", "stem": "vocals", "device": "cpu", "verified": True}


def separate_window(pcm, model):
    import numpy as np
    import torch
    import torch.nn.functional as F

    length = pcm.shape[1]
    if not pcm.any():
        return np.zeros(length, dtype=np.float32)
    with torch.inference_mode():
        mix = torch.from_numpy(pcm.astype(np.float32)) / 32768
        clean = denoise(mix)
        mean = clean.mean()
        scale = clean.std(unbiased=False).clamp_min(1e-6)
        # Supply context to the model even for very short inputs and final windows.
        standardized = F.pad((clean - mean) / scale, (0, WINDOW - length))
        stems = model(standardized[None])
        vocal = stems[0, model.sources.index("vocals"), :, :length].mean(dim=0)
        return (vocal * scale + mean).numpy()


def vocal_blocks(blocks, model):
    """Crossfade adjacent predictions and emit exactly one output per input sample."""
    import numpy as np

    pending = np.empty((2, WINDOW), dtype=np.int16)
    fade = np.arange(1, OVERLAP + 1, dtype=np.float32) / (OVERLAP + 1)
    size, tail = 0, None

    def blend(pcm):
        vocal = separate_window(pcm, model)
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
            "gain_db": 20 * math.log10(gain), "output_peak": peak * gain}


def prepare_audio(req):
    import av
    import json
    import numpy as np

    dest = Path(req["output"])
    with wave.open(req["original"], "rb") as original:
        frames = original.getnframes()
        if original.getframerate() != ASR_RATE:
            raise ValueError("AUDIO_TIMELINE: reference sample rate mismatch")
    model = load_separator(req["weights"])
    source = pcm_blocks(req["input"], MODEL_RATE, "stereo", req["max_audio_seconds"])
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=ASR_RATE)
    completed = 0
    # Bound working memory independently of the length of the lecture.
    with tempfile.TemporaryFile(dir=dest.parent) as samples:
        for block in vocal_blocks(source, model):
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(block[None, :]), format="fltp", layout="mono")
            frame.sample_rate = MODEL_RATE
            for converted in resampler.resample(frame):
                samples.write(converted.to_ndarray().astype("<f4", copy=False).tobytes())
            completed += len(block)
            print(json.dumps({"event": "progress", "completed": min(completed / MODEL_RATE, frames / ASR_RATE),
                              "total": frames / ASR_RATE, "unit": "秒音訊", "label": "去噪與 Demucs 人聲分離"}), flush=True)
        for converted in resampler.resample(None):
            samples.write(converted.to_ndarray().astype("<f4", copy=False).tobytes())
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
    return {"order": ["spectral_denoise", "demucs_vocals", "volume_normalize"],
            "separator": "torchaudio.HDEMUCS_HIGH_MUSDB_PLUS", "stem": "vocals", "device": "cpu",
            "sample_rate": ASR_RATE, "frames": frames, "normalization": levels}

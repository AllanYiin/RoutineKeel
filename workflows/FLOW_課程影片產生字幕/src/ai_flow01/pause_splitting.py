"""Cut overlong subtitles at measured internal speech pauses, on the source clock."""
from __future__ import annotations

import math
import unicodedata
import wave
from pathlib import Path

MIN_PAUSE_MS = 200
RATE = 16000


def analysis_track(original, vad, sha256):
    if not original or not Path(original).is_file():
        return None, "unavailable"
    original = Path(original)
    vocals = original.with_name("vocals.wav")
    expected = vad.get("analysis_audio_sha256")
    if (expected and original.name == "original.wav" and vocals.is_file()
            and not vocals.is_symlink() and sha256(vocals) == expected):
        return vocals, "demucs_vocals"
    return original, "original"


def acoustic_pauses(path, start_ms, end_ms, threshold):
    """Return ms gaps flanked by detected speech. None means evidence was unavailable."""
    if path is None:
        return None
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(threshold=threshold, min_speech_duration_ms=0,
                         min_silence_duration_ms=MIN_PAUSE_MS, speech_pad_ms=0,
                         max_speech_duration_s=math.inf)
    first, last = start_ms * 16, end_ms * 16
    pauses = []
    last_voice = None
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (RATE, 1, 2):
            return None
        if last > source.getnframes() + 16 or first >= source.getnframes():
            return None
        last = min(last, source.getnframes())
        # Adjacent blocks include one second of context on each side. Only commit
        # their non-overlapping cores so a block edge cannot supply a cut by itself.
        for begin in range(first, last, 60 * RATE):
            finish = min(last, begin + 60 * RATE)
            read_from, read_to = max(first, begin - RATE), min(last, finish + RATE)
            source.setpos(read_from)
            pcm = np.frombuffer(source.readframes(read_to - read_from), dtype="<i2")
            audio = pcm.astype(np.float32) / 32768
            for span in get_speech_timestamps(audio, options):
                a = max(begin, read_from + span["start"])
                b = min(finish, read_from + span["end"])
                if b <= a:
                    continue
                if last_voice is not None and a - last_voice >= MIN_PAUSE_MS * 16:
                    pauses.append((round(last_voice / 16), round(a / 16)))
                last_voice = b
    return pauses


def text_breaks(text):
    """Original character offsets at whitespace, punctuation, or CJK boundaries."""
    breaks = []
    for i in range(1, len(text)):
        left, right = text[i - 1], text[i]
        if left.isspace() or unicodedata.combining(right):
            continue
        if unicodedata.category(right) in {"Pe", "Pf", "Po"} or unicodedata.category(left) in {"Ps", "Pi"}:
            continue
        if (right.isspace() or left in "，、。！？；：!?,;:"
                or any(c.isalnum() and unicodedata.east_asian_width(c) in {"W", "F"} for c in (left, right))):
            breaks.append(i)
    return breaks


def word_boundaries(row, normalize_word):
    """Map verified ASR word boundaries to offsets in the actual, possibly edited, text."""
    words = row.get("words") or []
    text = row["text"]
    indices = [i for i, c in enumerate(text) if not c.isspace()]
    compact = "".join(text[i] for i in indices)
    position, previous = 0, float(row["start"])
    result = {}
    for w in words:
        start, end = float(w["start"]), float(w["end"])
        token = "".join(normalize_word(w["text"]).split())
        if (not token or not previous <= start < end <= row["end"]
                or not compact.startswith(token, position)):
            return {}
        if position:
            result[indices[position - 1] + 1] = (round(previous * 1000), round(start * 1000))
        position += len(token)
        previous = end
    return result if position == len(indices) else {}


def voiced_ms(start, end, pauses):
    # Include the half-pause inherited from a previous cut at either edge.
    return end - start - sum(max(0, min(end, q) - max(start, p)) for p, q in pauses)


def partition(row, start, end, limit, width_limit, pauses, normalize_word, display_width):
    """Times are integer ms. A missing pause never becomes a time- or text-based cut."""
    text = row["text"]
    boundaries = text_breaks(text)
    anchors = word_boundaries(row, normalize_word)
    parts, cuts, retained = [], [], []
    # The stack retains original text offsets through repeated cuts.
    pending = [(start, end, 0, len(text), pauses or [])]
    while pending:
        a, b, lo, hi, gaps = pending.pop()
        fragment = text[lo:hi].strip()
        if b - a <= limit and display_width(fragment) <= width_limit:
            parts.append((a, b, fragment))
            continue
        offsets = [i for i in boundaries if lo < i < hi and text[lo:i].strip() and text[i:hi].strip()]
        if not gaps or not offsets:
            reason = ("pause_audio_unavailable" if pauses is None else
                      "no_internal_speech_pause" if not gaps else "pause_text_indivisible")
            retained.append({"start_ms": a, "end_ms": b, "reason": reason})
            parts.append((a, b, fragment))
            continue
        index = max(range(len(gaps)), key=lambda i: (
            gaps[i][1] - gaps[i][0], -abs(sum(gaps[i]) - a - b), -gaps[i][0]))
        p, q = gaps[index]
        cut = (p + q) // 2
        # Only an inter-word gap covering the acoustic midpoint gives a direct
        # word-to-side assignment. A word spanning the pause is ambiguous.
        aligned = [i for i in offsets if i in anchors and anchors[i][0] <= cut <= anchors[i][1]]
        if aligned:
            boundary = min(aligned, key=lambda i: (abs(sum(anchors[i]) - 2 * cut), i))
            mapping = "word_timestamps"
        else:
            # Only text assignment is estimated. The measured audio cut stays fixed.
            left_voice = voiced_ms(a, p, pauses)
            right_voice = voiced_ms(q, b, pauses)
            units = {offset: n for n, offset in enumerate(offsets, 1)}
            target = (len(units) + 1) * left_voice / max(1, left_voice + right_voice)
            before = [i for i in offsets if i in anchors and anchors[i][1] < cut]
            after = [i for i in offsets if i in anchors and anchors[i][0] > cut]
            # When a timestamp spans silence, keep its neighbouring text boundaries
            # as alternatives. Count spoken tokens, not the letters in English words.
            choices = [before[-1], after[0]] if before and after else offsets
            boundary = min(choices, key=lambda i: (abs(units[i] - target), i))
            mapping = "estimated_text"
        cuts.append({"start_ms": a, "end_ms": b, "pause_start_ms": p, "pause_end_ms": q,
                     "cut_ms": cut, "text_offset": boundary, "text_mapping": mapping})
        pending.append((cut, b, boundary, hi, gaps[index + 1:]))
        pending.append((a, cut, lo, boundary, gaps[:index]))
    return parts, cuts, retained

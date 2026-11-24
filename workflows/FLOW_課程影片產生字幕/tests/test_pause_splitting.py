import csv
import hashlib
import wave

import numpy as np
import pytest
from ai_flow01 import pause_splitting as splitter
from ai_flow01 import portable_core as core


def row(text="甲乙丙丁", start=0, end=27):
    return {"id": 17, "text": text, "start": start, "end": end, "words": []}


CONFIG = {"max_line_width": 30, "max_lines": 2, "max_cue_seconds": 12,
          "low_logprob_threshold": -.8, "traditional_chinese": False}


def format_row(segment, pauses):
    log = []
    cues, issues = core.cues_from([segment], CONFIG, segment["end"],
                                  find_pauses=lambda a, b: pauses, pause_log=log)
    return cues, {i["reason"] for i in issues}, log


def test_longest_pause_is_chosen_even_when_off_centre_then_subdivided():
    segment = row()
    segment["words"] = [{"text": ch, "start": a, "end": b} for ch, a, b in
                        zip(segment["text"], [.1, 6.7, 14.1, 21.5], [6, 12, 18.4, 26.9])]
    cues, reasons, log = format_row(segment, [(6000, 6600), (12000, 14000), (18500, 21400)])
    assert [c["cut_ms"] for c in log[0]["cuts"]] == [19950, 13000, 6300]
    assert all(c["text_mapping"] == "word_timestamps" for c in log[0]["cuts"])
    assert not reasons and not log[0]["retained"]
    assert [c["text"] for c in cues] == list(segment["text"])
    assert [(c["start"], c["end"]) for c in cues] == [(0, 6.3), (6.3, 13), (13, 19.95), (19.95, 27)]


def test_equal_pauses_prefer_centre_then_earliest():
    _, _, log = format_row(row("甲乙丙丁", 0, 18), [(2000, 2600), (7200,7800), (10200,10800)])
    assert log[0]["cuts"][0]["cut_ms"] == 7500


@pytest.mark.parametrize("pauses,reason", [([], "no_internal_speech_pause"), (None, "pause_audio_unavailable")])
def test_unverified_pause_preserves_entire_segment_even_if_text_would_wrap(pauses, reason):
    segment = row("一二三四五。"*12+"最後一段，還在說話。", 31.5, 48)
    # Even a large ASR alignment gap cannot prove the speaker stopped.
    segment["words"] = [{"text":segment["text"][:10],"start":31.5,"end":34},
                        {"text":segment["text"][10:],"start":44,"end":48}]
    cues, reasons, log = format_row(segment, pauses)
    assert len(cues) == 1 and (cues[0]["start"], cues[0]["end"]) == (31.5, 48)
    assert cues[0]["text"].replace("\n", "") == segment["text"]
    assert reason in reasons and log[0]["cuts"] == []
    findings = core.validate(cues, {**CONFIG, "max_cps": 15, "min_cue_seconds": .5}, 48)
    assert {"line_limit","long_duration"} <= {f["reason"] for f in findings}


def test_missing_word_times_use_measured_cut_and_flag_text_only():
    segment = row("First phrase, then the answer.", 30, 48)
    segment["words"] = [{"text":"MISMATCH","start":30,"end":48}]
    cues, reasons, log = format_row(segment, [(37120, 38880)])
    assert [c["end"] for c in cues] == [38.0, 48.0]
    assert " ".join(c["text"] for c in cues) == segment["text"]
    assert log[0]["cuts"][0]["text_mapping"] == "estimated_text"
    assert reasons == {"pause_text_estimated"}


def test_word_spanning_pause_uses_nearby_tokens_and_excludes_parent_silence():
    segment = row("go up to the mountain and down to the blue water", 0, 24)
    segment["words"] = [{"text": token, "start": a, "end": b}
                        for token, (a, b) in zip(segment["text"].split(),
                        [(0,1), (1,2), (2,3), (3,4), (4,7.8), (7.8,14.1),
                         (14.1,15.5), (15.5,17), (17,18), (18,22), (22,24)])]
    _, reasons, log = format_row(segment, [(8000, 14000),(18000,21000)])
    first = log[0]["cuts"][0]
    assert first["cut_ms"] == 11000 and first["text_mapping"] == "estimated_text"
    assert first["text_offset"] in (segment["text"].index(" and"), segment["text"].index(" down"))
    assert "pause_text_estimated" in reasons
    assert splitter.voiced_ms(11000, 18000, [(8000, 14000), (18000,21000)]) == 4000


def test_one_token_and_continuous_child_stay_in_review():
    cues, reasons, _ = format_row(row("unbreakable", 0, 18), [(7000, 9000)])
    assert len(cues) == 1 and reasons == {"pause_text_indivisible"}
    cues, reasons, log = format_row(row("甲乙丙丁", 0, 25), [(2500, 3500)])
    assert [c["end"] for c in cues] == [3,25]
    assert log[0]["retained"] == [{"start_ms":3000,"end_ms":25000,"reason":"no_internal_speech_pause"}]
    assert "no_internal_speech_pause" in reasons


def test_exact_limit_uses_existing_formatting_and_skips_audio_lookup():
    cues, _ = core.cues_from([row("exactly twelve", 32.384, 44.384)], CONFIG, 50,
                             find_pauses=lambda *_: pytest.fail("short rows must not rescan audio"))
    assert [(c["start"], c["end"]) for c in cues] == [(32.384,44.384)]
    assert not core.validate(cues, {**CONFIG, "max_cps":15, "min_cue_seconds":.5}, 50)


def test_silero_pauses_exclude_edge_silence_and_use_original_clock(tmp_path, monkeypatch):
    from faster_whisper import vad
    f = tmp_path / "analysis.wav"
    with wave.open(str(f), "wb") as w:
        w.setparams((1,2,16000,0,"NONE","not compressed"))
        w.writeframes(np.zeros(19*16000, dtype="<i2").tobytes())
    def spans(audio, settings):
        assert len(audio) == 16*16000 and settings.speech_pad_ms == 0
        assert settings.min_silence_duration_ms == 200 and settings.threshold == .3
        return [{"start":16000,"end":5*16000},{"start":6*16000,"end":9*16000}]
    monkeypatch.setattr(vad, "get_speech_timestamps", spans)
    assert splitter.acoustic_pauses(f, 2000, 18000, .3) == [(7000,8000)]


def test_processed_track_requires_matching_hash(tmp_path):
    original, vocals = tmp_path / "original.wav", tmp_path / "vocals.wav"
    original.write_bytes(b"source"); vocals.write_bytes(b"stem")
    assert splitter.analysis_track(original, {"analysis_audio_sha256":hashlib.sha256(b"stem").hexdigest()}, core.sha256) == (vocals,"demucs_vocals")
    assert splitter.analysis_track(original, {"analysis_audio_sha256":"bad"}, core.sha256) == (original,"original")


def test_rebuild_retains_unsplit_silence_in_srt_and_review(review_fixture):
    folder, config = review_fixture
    segment = row("原句。原文保留。", 1, 18)
    core.write_json(folder/"transcript.json", [segment])
    core.write_json(folder/"manifest.json", {"source":"","duration":20})
    with wave.open(str(folder/"audio.wav"), "wb") as w:
        w.setparams((1,2,16000,0,"NONE","not compressed"))
        w.writeframes(np.zeros(20*16000, dtype="<i2").tobytes())
    core.build(folder, config, source_audio=folder/"audio.wav")
    assert core.read_json(folder/"QA.json")["decision"] == "FAIL"
    cues = core.read_json(folder/"cues.json")
    assert cues == [{"start":1.0,"end":18.0,"text":segment["text"],"source_id":17}]
    with (folder/"review.csv").open(encoding="utf-8-sig",newline="") as f:
        assert {"no_internal_speech_pause","qa_long_duration"} <= {r["reason"] for r in csv.DictReader(f)}
    assert core.read_json(folder/"summary.json")["pause_retained_count"] == 1

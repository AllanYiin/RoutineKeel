import json

import pytest
from ai_flow01.review import review
from ai_flow01.subtitles import cues_from_segments, render, validate_segments
from flow_base.contracts import LLM
from flow_base.llm import Ledger
from flow_base.runtime import FlowError, Runtime


def settings(**kwargs):
    return LLM(enabled=True, model="test-model", pricing_verified=True, pricing_note="test-only rates, not a quote",
               input_usd_per_million=1, output_usd_per_million=2, **kwargs)


def segments():
    return [{"id": 0, "start": 1.0, "end": 3.0, "text": "今天學習機器學習", "words": []},
            {"id": 1, "start": 9.0, "end": 11.0, "text": "模型不能改變原意", "words": []}]


def response(edits, usage=True):
    r = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"edits": edits}, ensure_ascii=False)}}]}
    if usage:
        r["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
    return r


def test_silence_timeline_preserved():
    cues, _ = cues_from_segments(segments())
    srt = render(cues)
    assert "00:00:09,000 --> 00:00:11,000" in srt
    assert render(cues, True).startswith("WEBVTT\n")


def test_empty_speech_valid_export():
    assert render([]) == ""
    assert render([], True).startswith("WEBVTT")


def test_word_timed_split():
    s = [{"id": 0, "start": 0, "end": 6, "text": "abcdefghijklmnopqrstuvwx", "words": [
        {"word": "abcdefgh", "start": 0, "end": 2}, {"word": "ijklmnop", "start": 2, "end": 4}, {"word": "qrstuvwx", "start": 4, "end": 6}]}]
    cues, warnings = cues_from_segments(s, width=8)
    assert [(c["start"], c["end"]) for c in cues] == [(0, 4), (4, 6)]
    assert not warnings


def test_latin_wrap_respects_two_lines_not_only_character_count():
    texts = [' And', ' so,', ' my', ' fellow', ' Americans,', ' ask', ' not', ' what']
    words = [{'word':w, 'start':i*0.5, 'end':(i+1)*0.5} for i,w in enumerate(texts)]
    segments = [{'id':0,'start':0,'end':4,'text':''.join(texts).strip(),'words':words}]
    cues, warnings = cues_from_segments(segments, width=22)
    assert not warnings
    assert all(len(c['text'].splitlines()) <= 2 for c in cues)


def test_modified_text_keeps_segment_time():
    s = segments()
    s[0]["text"] = "長" * 70
    cues, warnings = cues_from_segments(s)
    assert cues[0]["start"] == 1 and cues[0]["end"] == 3
    assert warnings[0]["reason"] == "needs_manual_resegmentation"


def test_invalid_timeline_rejected():
    s = segments()
    s[1]["start"] = 2
    with pytest.raises(FlowError):
        validate_segments(s)












def test_concurrent_reservation_atomic(tmp_path):
    import concurrent.futures
    r = Runtime(tmp_path)
    ledger = Ledger(r, "job", settings(max_calls=1))
    def reserve(i):
        try:
            return ledger.reserve(str(i), 100)
        except FlowError:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(x is not None for x in results) == 1


def test_no_pricing_no_llm():
    with pytest.raises(ValueError):
        LLM(enabled=True, model="anything")


def test_no_llm_in_offline_mode(tmp_path):
    with pytest.raises(FlowError) as e:
        review(Runtime(tmp_path), "job", segments(), settings(), [], offline=True)
    assert e.value.code == "LOCAL_LLM_FORBIDDEN"


def test_local_review_never_calls_provider(tmp_path, monkeypatch):
    import flow_base.llm
    from ai_flow01.contracts import LocalReviewPolicy
    monkeypatch.setattr(flow_base.llm, 'http_call', lambda *_: pytest.fail('no provider calls allowed'))
    revised, report = review(Runtime(tmp_path), 'job', segments(), LocalReviewPolicy(), [])
    assert revised == segments() and report['status'] == 'REVIEW_REQUIRED'
    assert report['usage']['calls'] == report['usage']['tokens'] == 0
    with pytest.raises(FlowError, match='0 次'):
        review(Runtime(tmp_path), 'job', segments(), LocalReviewPolicy(), [], transport=lambda *_: pytest.fail('called'))

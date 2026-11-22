import sys
from pathlib import Path

import pytest

FLOW = Path(__file__).resolve().parents[1]
PRODUCT = FLOW.parents[1]
sys.path.insert(0, str(PRODUCT / 'flow_base/src'))
sys.path.insert(0, str(FLOW / 'src'))



@pytest.fixture
def review_fixture(tmp_path,monkeypatch):
    from ai_flow01 import portable_core as core
    from ai_flow01.contracts import LocalReviewPolicy
    from review_fixtures import make_wav

    monkeypatch.setattr(core,'traditional',lambda text:text)
    config={'max_line_width':30,'max_lines':2,'max_cps':15,'min_cue_seconds':0.5,'max_cue_seconds':12,
            'low_logprob_threshold':-0.8,'spot_check_count':10,'llm':LocalReviewPolicy().model_dump()}
    core.write_json(tmp_path/'manifest.json',{'source':'','duration':12})
    core.write_json(tmp_path/'transcript.json',[{'id':0,'start':1,'end':3,'text':'測試字幕內容','avg_logprob':-0.1,'words':[]}])
    make_wav(tmp_path/'audio.wav')
    return tmp_path,config

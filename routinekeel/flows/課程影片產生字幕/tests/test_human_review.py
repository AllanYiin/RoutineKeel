import csv
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from ai_flow01 import portable_core as core, worker, human_review
from ai_flow01.contracts import Options
from test_local_pipeline import review_fixture


def build_fixture(review_fixture):
    out,config=review_fixture
    rows=core.read_json(out/'transcript.json')
    rows[0]['avg_logprob']=-1.1
    core.write_json(out/'transcript.json',rows)
    core.write_json(out/'asr_details.json',{'recognizer_text':{'0':'辨識原文'}})
    core.write_json(out/'review_config.json',config)
    core.build(out,config,source_audio=out/'audio.wav')
    # Execute the actual isolated worker loader when finishing the delivery.
    worker.finish_review_files(out)
    return out,config,core.read_json(out/'review_delivery.json')


def answers(out,delivery,text='確認後文字'):
    with (out/delivery['table']).open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    rows[0]['確認文字']=text
    target=out/'answers.csv'
    with target.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=human_review.HEADERS);writer.writeheader();writer.writerows(rows)
    return target,rows


def test_readable_bundle_audio_csv_transcript_and_zip(review_fixture):
    out,config,d=build_fixture(review_fixture)
    page=(out/'review.html').read_text()
    assert 'data:audio/wav;base64,' in page
    assert '辨識原文' in page and '辨識信心偏低' in page
    assert '<textarea' in page and '下載填答 CSV' in page
    assert '00:00:01.000' in page
    assert '[00:00:01.000' in (out/d['transcript']).read_text()
    assert d['review_segments']==1
    with zipfile.ZipFile(out/d['archive']) as z:
        assert z.testzip() is None
        assert {'review.html',d['table'],d['transcript'],'subtitles.srt','subtitles.vtt','QA.json'}<=set(z.namelist())
        assert 'transcript.json' not in z.namelist()


def test_csv_roundtrip_rebuild_and_no_automatic_approval(review_fixture,monkeypatch):
    out,config,d=build_fixture(review_fixture)
    target,_=answers(out,d)
    core.write_json(out/'edits.json',{'0':'先前人工文字'})
    core.write_json(out/'approval.json',{'reviewer':'test fixture, not a real reviewer'})
    monkeypatch.setattr(worker,'domain_core',lambda:core)
    monkeypatch.setattr(worker,'load_model',lambda *a:pytest.fail('must not recognize'))
    result=worker.review_operation({'operation':'import','output_dir':str(out),'csv_path':str(target),'source_audio':str(out/'audio.wav')})
    assert result['decision']=='REVIEW_REQUIRED'
    assert core.read_json(out/'edits.json')['0']=='確認後文字'
    assert '確認後文字' in (out/'subtitles.srt').read_text()
    assert not (out/'approval.json').exists()
    assert list((out/'versions').glob('*.edits.json'))
    assert list((out/'versions').glob('*.approval.json'))
    assert list((out/'versions').glob('*.srt'))
    assert '確認後文字' in (out/'review.html').read_text()
    # A downloaded CSV from before rebuilding cannot overwrite the new revision.
    with pytest.raises(ValueError,match='版本'):human_review.import_answers(out,target)


@pytest.mark.parametrize('change',['revision','unknown','duplicate'])
def test_bad_csv_is_rejected_before_writing(review_fixture,change):
    out,_,d=build_fixture(review_fixture)
    target,rows=answers(out,d)
    if change=='revision':rows[0]['資料版本']='stale'
    if change=='unknown':rows[0]['片段']='999'
    if change=='duplicate':rows.append(dict(rows[0]))
    with target.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=human_review.HEADERS);w.writeheader();w.writerows(rows)
    before=(out/'subtitles.srt').read_bytes()
    with pytest.raises(ValueError):human_review.import_answers(out,target)
    assert not (out/'edits.json').exists()
    assert before==(out/'subtitles.srt').read_bytes()


def test_html_escapes_source_and_csv_formula_text(review_fixture):
    out,config=review_fixture
    rows=core.read_json(out/'transcript.json');rows[0].update(text='=1+1 </script><script>alert(1)</script>',avg_logprob=-1.1)
    core.write_json(out/'transcript.json',rows)
    core.build(out,config,source_audio=out/'audio.wav')
    page=(out/'review.html').read_text()
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;' in page
    assert '\\u003c/script' in page
    d=core.read_json(out/'review_delivery.json')
    with (out/d['table']).open(encoding='utf-8-sig',newline='') as f:r=next(csv.DictReader(f))
    assert r['ASR原文'].startswith("'=1+1")


def test_no_issues_still_has_human_entry(review_fixture):
    out,config=review_fixture
    core.build(out,config,source_audio=out/'audio.wav')
    assert '等待真人聽審' in (out/'review.html').read_text()
    assert core.read_json(out/'review_delivery.json')['review_segments']==0


@pytest.mark.parametrize('value',[{}, {'initial_prompt':''},{'initial_prompt':'   '}])
def test_default_and_legacy_blank_prompts_are_nonempty(value):
    assert Options(**value).initial_prompt=='繁體中文'


def test_explicit_prompt_and_disabled_are_preserved():
    assert Options(initial_prompt='繁體中文。生成式 AI。').initial_prompt=='繁體中文。生成式 AI。'
    assert Options(initial_prompt_enabled=False).initial_prompt==''
    with pytest.raises(ValueError):Options(initial_prompt='字'*301)


def test_large_audio_uses_relative_clips_in_share_zip(review_fixture, monkeypatch):
    out,config,d=build_fixture(review_fixture)
    monkeypatch.setattr(human_review,'EMBED_AUDIO_LIMIT',0)
    rows=core.read_json(out/'transcript.json')
    issues=[{'id':0,'reason':'low_asr_confidence','start':1,'end':3,'text':rows[0]['text']}]
    human_review.render(out,rows,issues,{0:'review_clips/000000.wav'},core.read_json(out/'QA.json'))
    human_review.package(out)
    assert 'data:audio/' not in (out/'review.html').read_text()
    assert 'src="review_clips/000000.wav"' in (out/'review.html').read_text()
    with zipfile.ZipFile(out/d['archive']) as z:
        assert 'review_clips/000000.wav' in z.namelist()


def test_worker_script_loads_review_modules_in_isolated_python(review_fixture):
    out,config,d=build_fixture(review_fixture)
    # This exercises the same -I file loader used by the runtime, not only imports in pytest.
    config['traditional_chinese']=False
    core.write_json(out/'review_config.json',config)
    request=out/'request.json';response=out/'response.json'
    core.write_json(request,{'action':'review_operation','operation':'build','output_dir':str(out),
        'source_audio':str(out/'audio.wav'),'response_path':str(response)})
    result=subprocess.run([sys.executable,'-X','utf8','-I',worker.__file__,str(request)],
                          capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr
    assert core.read_json(response)['review']=='review.html'
    assert (out/d['archive']).is_file()


@pytest.mark.parametrize('changed',[False,True])
def test_import_failure_preserves_untouched_approval_status(tmp_path,monkeypatch,changed):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from ai_flow01 import adapter
    from flow_base.runtime import FlowError,sha256
    home=tmp_path/'share_states';home.mkdir();audio=home/'audio.wav';audio.write_bytes(b'fixture audio')
    out=tmp_path/'job';artifacts=out/'artifacts';artifacts.mkdir(parents=True)
    core.write_json(artifacts/'flow_context.json',{'profile':'asr-cpu','model':'whisper-large-v3',
        'source_audio':'audio.wav','source_audio_sha256':sha256(audio)})
    core.write_json(out/'report.json',{'status':'PASS','output_layout':'categorized-v1'})
    def worker_failure(*args,**kwargs):
        if changed:core.write_json(artifacts/'edits.json',{'0':'staged correction'})
        raise FlowError('FIXTURE','synthetic import failure')
    runtime=SimpleNamespace(home=home,prepare_profile=lambda *a:None,lock=lambda *a,**k:nullcontext(),worker=worker_failure)
    monkeypatch.setattr(adapter,'configure_profile',lambda *a:None)
    with pytest.raises(FlowError):adapter.Adapter().review_operation(runtime,out,'import',csv_path=str(tmp_path/'answers.csv'))
    assert core.read_json(out/'report.json')['status']==('failed' if changed else 'PASS')

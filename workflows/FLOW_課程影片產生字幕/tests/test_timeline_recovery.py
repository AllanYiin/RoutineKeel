import copy
import wave
from pathlib import Path

import pytest
from ai_flow01 import adapter as domain
from ai_flow01 import portable_core as core
from ai_flow01 import worker
from ai_flow01.contracts import subtitle_workflow
from ai_flow01.recovery import recover
from ai_flow01.resources import catalogue
from ai_flow01.timeline import normalize_word_timelines
from flow_base.runtime import FlowError, Runtime, atomic_json, read_json, sha256


@pytest.mark.parametrize('words', [
    [{'start':0.5,'end':1.5,'word':'a'}],
    [{'start':1.5,'end':2.5,'word':'a'}],
    [{'start':1.5,'end':1.4,'word':'a'}],
    [{'start':1.5,'end':1.5,'word':'a'}],
    [{'start':1.5,'end':1.7,'word':'a'},{'start':1.3,'end':1.4,'word':'b'}],
    [{'start':1.1,'end':1.7,'word':'a'},{'start':1.6,'end':1.9,'word':'b'}],
])
def test_bad_words_preserve_text_and_segment_bounds_with_explicit_issue(words):
    original=[{'id':20,'start':1.0,'end':2.0,'text':'ab','words':words}]
    before=copy.deepcopy(original)
    rows,issues=normalize_word_timelines(original,3)
    assert original==before
    assert rows[0]=={**original[0],'words':[]}
    assert issues[0]['id']==20 and issues[0]['raw_words']==words
    assert issues[0]['handling']=='segment_timing_only'


def test_valid_words_are_unchanged_and_invalid_segment_still_fails():
    rows=[{'id':0,'start':1.0,'end':2.0,'text':'ab','words':[
        {'start':1.0,'end':1.5,'word':'a'},{'start':1.5,'end':2.0,'word':'b'}]}]
    assert normalize_word_timelines(rows,3)==(rows,[])
    rows[0]['end']=0.5
    with pytest.raises(FlowError,match='段落'):normalize_word_timelines(rows,3)


@pytest.fixture(params=['flat', 'nested'])
def failed_work(tmp_path,monkeypatch,request):
    nested=request.param=='nested'
    if nested:(tmp_path/'workflows').mkdir()
    base=tmp_path/'flow_base' if nested else tmp_path
    runtime=Runtime(base/'share_states');runtime.initialize();runtime.logs=base/'share_logs'
    source=tmp_path/'lecture.fixture';source.write_bytes(b'synthetic source, not user course')
    flow=subtitle_workflow().model_copy(update={'profile':'asr-cpu'})
    flow.options.initial_prompt=''  # Historical 0.3.2 snapshot had an empty prompt.
    flow.options.device='cpu';flow.options.language='en';flow.options.traditional_chinese=False
    folder=tmp_path/'workflows/FLOW_課程影片產生字幕' if nested else tmp_path/'課程影片產生字幕'
    out=folder/'outputs/failed';out.mkdir(parents=True)
    audio=runtime.home/'artifacts/extract/result.wav';audio.parent.mkdir(parents=True)
    with wave.open(str(audio),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*16000*22)
    vad_path=runtime.home/'artifacts/vad/result.json';vad_path.parent.mkdir(parents=True)
    region_audio=vad_path.parent/'region.wav';region_audio.write_bytes(audio.read_bytes())
    region={'id':0,'start':0,'end':22,'audio_file':'region.wav','sha256':sha256(region_audio)}
    vad={'regions':[region],'duration':22,'sample_rate':16000,'source_sha256':sha256(audio),
         'source_audio':audio.relative_to(runtime.home).as_posix(),'source_audio_sha256':sha256(audio)}
    atomic_json(vad_path,vad);runtime.remember('extract',audio);runtime.remember('vad',vad_path)
    model=catalogue()[flow.model]
    settings={'model':flow.model,'model_revision':model.revision,'device':'cpu','language':'en',
              'beam_size':5,'prompt':'','traditional_chinese':False}
    segments=[]
    for i in range(21):
        start=i+0.1;end=i+0.9
        segments.append({'start':start,'end':end,'text':'hi','recognizer_text':'hi',
            'words':[{'start':start if i!=20 else start-0.5,'end':end,'word':'hi'}],
            'metrics':{'avg_logprob':-0.1,'no_speech_prob':0.01,'compression_ratio':1.0}})
    result={'segments':segments,'language':'en','device':'cpu','fallback':None}
    legacy=read_json(Path(domain.__file__).with_name('recovery_compat.json'))['workers'][0]['sha256']
    signature=worker.value_hash({'schema':1,'region':region,'settings':settings,'worker':legacy})
    raw_path=runtime.home/'artifacts/asr/result.json'
    atomic_json(raw_path.parent/'region_checkpoints/region_00000.json',
        {'signature':signature,'sha256':worker.value_hash(result),'result':result})
    raw={'segments':[{'id':i,**{k:s[k] for k in ('start','end','text','words')}} for i,s in enumerate(segments)],
         'recognizer_text':{str(i):s['recognizer_text'] for i,s in enumerate(segments)},
         'recognition_metrics':{str(i):{**s['metrics'],'region_id':0} for i,s in enumerate(segments)},
         'vad_regions':[region],'region_progress':[{'region_id':0,'status':'executed'}],
         'duration':22,'model':flow.model,'model_revision':model.revision,'device':'cpu','engine':'faster-whisper'}
    atomic_json(raw_path,raw)
    snapshot={'flow_id':'flow_test','job_id':'job_test','run_id':'run_old','workflow':flow.model_dump(),
              'prepared':{'device':'cpu'},'source':{'workspace_relative':source.name},'input_hash':sha256(source)}
    atomic_json(runtime.home/'jobs/flow_test/job_test/snapshot.json',snapshot)
    failed={'flow_id':'flow_test','job_id':'job_test','run_id':'run_old','status':'failed','error':'ValidationError',
            'output_layout':'categorized-v1','steps':[
                {'id':'extract','handler':'media.extract','status':'executed','cache_key':'extract'},
                {'id':'vad','handler':'speech.vad','status':'executed','cache_key':'vad'}]}
    atomic_json(out/'report.json',failed)
    log=runtime.logs/'flow_test/run_new';log.mkdir(parents=True)
    runtime.action_context={'flow_id':'flow_test','run_id':'run_new','folder':folder,'logdir':log}
    monkeypatch.setattr(domain,'configure_profile',lambda *a:None)
    monkeypatch.setattr(runtime,'environment',lambda _: (tmp_path,source))
    monkeypatch.setattr(runtime,'prepare_profile',lambda *a,**k:pytest.fail('must not install'))
    monkeypatch.setattr(worker,'load_model',lambda *a:pytest.fail('must not load or transcribe'))
    monkeypatch.setattr(worker,'domain_core',lambda:core)
    monkeypatch.setattr(core,'traditional',lambda text:text)
    calls=[]
    def local_worker(profile,request,**kwargs):
        calls.append(request['action']);assert request['action']=='qa_bundle'
        return worker.qa_bundle(request)
    monkeypatch.setattr(runtime,'worker',local_worker)
    return runtime,domain.Adapter(),out,raw_path,vad,flow,calls


def test_recovery_exports_reviewable_subtitles_without_recognition(failed_work):
    runtime,adapter,out,raw_path,_vad,_flow,calls=failed_work
    original=raw_path.read_bytes()
    report=recover(runtime,adapter)
    assert report['status']=='REVIEW_REQUIRED' and report['recovery']['asr_calls']==0
    assert report['recovery']['timing_issue_ids']==[20]
    assert report['run_id']=='run_new' and report['recovery']['source_run_id']=='run_old'
    assert calls==['qa_bundle'] and raw_path.read_bytes()==original
    assert (out/'artifacts/subtitles.srt').is_file() and (out/'artifacts/subtitles.vtt').is_file()
    assert (out/'artifacts/review_clips/000020.wav').is_file()
    assert 'invalid_word_timeline' in (out/'artifacts/review.csv').read_text(encoding='utf-8-sig')
    assert read_json(out/'docs/before-run_new.json')['status']=='failed'
    assert read_json(out/'deliverables.json')['status']=='REVIEW_REQUIRED'
    core.build(out/'artifacts',read_json(out/'artifacts/review_config.json'),source_audio=runtime.home/'artifacts/extract/result.wav')
    assert 'invalid_word_timeline' in (out/'artifacts/review.csv').read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('change',['checkpoint','raw','model','input','missing_audio','human_edits'])
def test_recovery_refuses_untrusted_or_changed_inputs(failed_work,change):
    runtime,adapter,out,raw_path,_vad,_flow,calls=failed_work
    if change=='checkpoint':
        p=raw_path.parent/'region_checkpoints/region_00000.json';v=read_json(p);v['sha256']='bad';atomic_json(p,v)
    elif change=='raw':
        v=read_json(raw_path);v['segments'][0]['text']='changed';atomic_json(raw_path,v)
    elif change=='model':
        v=read_json(raw_path);v['model_revision']='changed';atomic_json(raw_path,v)
    elif change=='input':(runtime.workspace_root/'lecture.fixture').write_bytes(b'changed')
    elif change=='missing_audio':(runtime.home/'artifacts/extract/result.wav').unlink()
    else:atomic_json(out/'artifacts/edits.json',{'0':'human'})
    with pytest.raises(FlowError):recover(runtime,adapter)
    assert not calls and read_json(out/'report.json')['status']=='failed'


def test_normal_transcribe_handler_keeps_raw_and_uses_fallback(failed_work,monkeypatch):
    from ai_flow01.subtitles import transcribe
    from flow_base.engine import Context
    runtime,_adapter,out,raw_path,_vad,flow,_calls=failed_work
    raw=read_json(raw_path)
    def worker_reply(profile,request,**kwargs):atomic_json(Path(request['output']),raw)
    monkeypatch.setattr(runtime,'worker',worker_reply)
    target=runtime.home/'artifacts/new/result.json'
    ctx=Context(runtime,flow,runtime.workspace_root/'lecture.fixture',out,'j',{'device':'cpu'},True,runtime.logs)
    transcribe(ctx,flow.steps[2],{'vad':runtime.home/'artifacts/vad/result.json'},target)
    assert read_json(target.with_suffix('.raw.json'))==raw
    assert read_json(target)['segments'][20]['words']==[]
    assert read_json(target)['timing_issues'][0]['id']==20

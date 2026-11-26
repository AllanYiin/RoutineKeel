import json
import sys
import types
import zipfile

import pytest
from ai_flow01 import portable_core as core
from ai_flow01 import worker
from ai_flow01.contracts import LocalReviewPolicy, Options, subtitle_workflow
from review_fixtures import make_wav


@pytest.mark.parametrize("mode", ["legacy", "persistent", "archive"])
def test_region_resume_preserves_offsets_and_skips_finished_regions(tmp_path, monkeypatch, mode):
    class Audio:
        def astype(self, _):
            return self

        def __truediv__(self, _):
            return self

    monkeypatch.setitem(sys.modules, 'numpy', types.SimpleNamespace(float32=None))
    monkeypatch.setitem(sys.modules, 'opencc', types.SimpleNamespace(OpenCC=lambda _: types.SimpleNamespace(convert=lambda t:t)))
    monkeypatch.setattr(worker, 'read_pcm', lambda _: Audio())
    regions = []
    for index, start in enumerate((1, 9, 15)):
        path = tmp_path / f'{index}.wav'
        make_wav(path, 2)
        regions.append({'id': index, 'start': start, 'end': start + 2,
                        'audio_file': path.name, 'sha256': worker.file_hash(path)})
    persistent_checkpoints = mode != "legacy"
    archive_path = tmp_path / "vad.zip"
    archive_reads = []
    if mode == "archive":
        with zipfile.ZipFile(archive_path, "w") as archive:
            for region in regions:
                source = tmp_path / region.pop("audio_file")
                region.pop("sha256")
                region.update(archive_member=source.name, size=source.stat().st_size)
                archive.write(source, source.name)
                source.unlink()
        original_open = zipfile.ZipFile.open

        def track_reads(archive, member, *args, **kwargs):
            archive_reads.append(member)
            return original_open(archive, member, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "open", track_reads)
    manifest = tmp_path / 'vad.json'
    worker.save(manifest, {'duration': 17, 'regions': regions})
    calls = []
    loads = []

    class Model:
        fail = True

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2 and self.fail:
                raise RuntimeError('simulated interruption')
            word = types.SimpleNamespace(start=0.1, end=0.8, word=' hello')
            segment = types.SimpleNamespace(start=0.1, end=0.8, text=' hello', words=[word],
                                            avg_logprob=-0.1, no_speech_prob=0.01)
            return iter([segment]), types.SimpleNamespace(language='en')

    model = Model()

    def load_model(_):
        loads.append('cpu')
        return model, 'cpu', None

    monkeypatch.setattr(worker, 'load_model', load_model)
    req = {'vad_path': str(manifest), 'output': str(tmp_path / 'out.json'), 'language': 'en',
           'model': 'whisper-large-v3', 'device': 'cpu', 'prompt': Options().initial_prompt}
    checkpoints = tmp_path / 'region_checkpoints'
    if persistent_checkpoints:
        checkpoints = tmp_path / 'session' / 'region_checkpoints'
        req['checkpoints'] = str(checkpoints)
    if mode == "archive":
        req["speech_archive"] = str(archive_path)
    with pytest.raises(RuntimeError, match='interruption'):
        worker.transcribe(req)
    assert (checkpoints / 'region_00000.json').is_file()
    assert not (tmp_path / 'out.json').exists()
    if mode == "archive":
        assert archive_reads == ['0.wav', '1.wav']
        assert core.read_json(checkpoints / 'progress.json') == {
            'completed': 1, 'total': 3, 'cached': 0, 'executed': 1, 'next_region_id': 1,
        }
        archive_reads.clear()
    model.fail = False
    output = tmp_path / 'restart' / 'out.json' if persistent_checkpoints else tmp_path / 'out.json'
    req['output'] = str(output)
    worker.transcribe(req)
    data = json.loads(output.read_text())
    assert len(calls) == 4
    assert loads == ['cpu', 'cpu']
    assert [(s['start'], s['end']) for s in data['segments']] == [(1.1, 1.8), (9.1, 9.8), (15.1, 15.8)]
    assert data['segments'][1]['words'][0]['start'] == 9.1
    assert [r['status'] for r in data['region_progress']] == ['cached', 'executed', 'executed']
    assert all(c['initial_prompt'] == '繁體中文' for c in calls)
    assert all(not c['vad_filter'] and not c['condition_on_previous_text'] for c in calls)
    if mode == "archive":
        assert archive_reads == ['0.wav', '1.wav', '2.wav']
        assert core.read_json(checkpoints / 'progress.json') == {
            'completed': 3, 'total': 3, 'cached': 1, 'executed': 2, 'next_region_id': None,
        }
        worker.transcribe(req)
        assert len(calls) == 4
        assert loads == ['cpu', 'cpu']
        assert core.read_json(checkpoints / 'progress.json')['cached'] == 3




def test_no_warnings_still_requires_human_review(review_fixture):
    out,config=review_fixture
    core.build(out,config,source_audio=out/'audio.wav')
    qa=core.read_json(out/'QA.json')
    assert qa['decision']=='REVIEW_REQUIRED' and qa['issue_count']==0
    assert qa['llm_calls']==qa['llm_tokens']==0
    assert not (out/'approval.json').exists()


def test_doubt_audio_and_approval_invalidated_after_edit(review_fixture):
    out,config=review_fixture
    rows=core.read_json(out/'transcript.json');rows[0]['avg_logprob']=-1.1;core.write_json(out/'transcript.json',rows)
    core.build(out,config,source_audio=out/'audio.wav')
    qa=core.read_json(out/'QA.json')
    assert qa['decision']=='REVIEW_REQUIRED' and qa['review_audio_count']==1
    assert (out/'review_clips/000000.wav').exists()
    with pytest.raises(ValueError,match='count'):core.approve(out,config,'automated fixture, not a real human',0,source_audio=out/'audio.wav')
    core.approve(out,config,'automated fixture, not a real human',qa['issue_count'],source_audio=out/'audio.wav')
    assert core.read_json(out/'QA.json')['decision']=='PASS'
    core.write_json(out/'edits.json',{'0':'人工修改內容'})
    core.build(out,config,force_rebuild=True,source_audio=out/'audio.wav')
    assert core.read_json(out/'QA.json')['decision']=='REVIEW_REQUIRED'
    assert list((out/'versions').glob('*.srt'))


def test_hard_qa_cannot_be_approved(review_fixture):
    out,config=review_fixture
    rows=core.read_json(out/'transcript.json');rows[0].update(start=1,end=1.05,text='非常快速而無法閱讀的字幕內容')
    core.write_json(out/'transcript.json',rows)
    config['max_cps']=0.1
    core.build(out,config,source_audio=out/'audio.wav')
    assert core.read_json(out/'QA.json')['decision']=='FAIL'
    with pytest.raises(ValueError,match='findings'):core.approve(out,config,'fixture',1,source_audio=out/'audio.wav')


def test_packet_is_bounded_and_redacted():
    rows=[{'id':i,'text':'hello@example.com https://example.com '+('字'*130),'start':i,'end':i+1} for i in range(40)]
    issues=[{'id':r['id'],'reason':'low_asr_confidence'} for r in rows]
    packet,capped=core.review_packet(rows,issues,{'llm':LocalReviewPolicy().model_dump()})
    assert capped and len(packet)<=20
    assert sum(len(r['text'])+len(r['context_before'])+len(r['context_after']) for r in packet)<=3000
    assert 'hello@example.com' not in json.dumps(packet) and '[EMAIL]' in json.dumps(packet)


def test_local_policy_and_defaults_are_strict():
    with pytest.raises(ValueError):Options(llm={'enabled':True})
    with pytest.raises(ValueError):Options(llm={'max_calls':1})
    with pytest.raises(ValueError):LocalReviewPolicy(max_segments=21)
    flow=subtitle_workflow()
    assert flow.model=='whisper-large-v3'
    assert flow.options.max_line_chars==30 and flow.options.max_cue_seconds==12
    assert [s.handler for s in flow.steps]==['media.extract','speech.vad','speech.transcribe','text.review','subtitle.export']


def test_human_notes_survive_rebuild_and_old_table_is_archived(review_fixture):
    import csv
    out,config=review_fixture
    rows=core.read_json(out/'transcript.json');rows[0]['avg_logprob']=-1.1;core.write_json(out/'transcript.json',rows)
    core.build(out,config,source_audio=out/'audio.wav')
    path=out/'review.csv'
    with path.open(encoding='utf-8-sig',newline='') as stream: notes=list(csv.DictReader(stream))
    notes[0]['confirmed_text']='已聽審的人工註記'
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(notes[0]));writer.writeheader();writer.writerows(notes)
    core.build(out,config,source_audio=out/'audio.wav')
    assert '已聽審的人工註記' in path.read_text(encoding='utf-8-sig')
    core.write_json(out/'edits.json',{'0':'修訂後文字'})
    core.build(out,config,force_rebuild=True,source_audio=out/'audio.wav')
    assert any('已聽審的人工註記' in p.read_text(encoding='utf-8-sig') for p in (out/'versions').glob('*.review.csv'))


def test_non_traditional_output_preserves_punctuation():
    assert core.edited_rows([{'id':0,'text':'hello, world!'}],{},traditional_chinese=False)[0]['text']=='hello, world!'


def test_packet_redacts_before_truncating_and_zero_context_is_empty():
    rows=[{'id':i,'text':'a'*70+'@example.com 123456789','start':i,'end':i+1} for i in range(3)]
    issues=[{'id':1,'reason':'low_asr_confidence'}]
    packet,_=core.review_packet(rows,issues,{'llm':LocalReviewPolicy(context_chars=0).model_dump()})
    assert packet[0]['context_before']==packet[0]['context_after']==''
    packet,_=core.review_packet(rows,issues,{'llm':LocalReviewPolicy().model_dump()})
    assert all(len(packet[0][k])<=60 for k in ('context_before','context_after'))
    assert '@example' not in json.dumps(packet) and '123456789' not in json.dumps(packet)


def test_recognition_rerun_protects_human_revisions(tmp_path):
    from ai_flow01.adapter import Adapter
    from flow_base.runtime import FlowError
    for name in ('edits.json','timing_edits.json','approval.json'):
        path=tmp_path/name;path.write_text('{}')
        with pytest.raises(FlowError) as exc: Adapter().check_output(None,None,tmp_path)
        assert exc.value.code=='HUMAN_REVIEW_PRESENT' and path.read_text()=='{}'
        path.unlink()


def test_binary_audio_cache_is_not_parsed_as_json(tmp_path):
    from ai_flow01.adapter import Adapter
    audio=tmp_path/'result.wav';make_wav(audio,1)
    assert Adapter().artifact_valid(None,'media.extract',audio)
    assert not Adapter().artifact_valid(None,'speech.vad',audio)

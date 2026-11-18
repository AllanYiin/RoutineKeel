"""Recover completed local ASR after validation failure, without decoding again."""
from pathlib import Path
from flow_base.engine import Context
from flow_base.runtime import FlowError, atomic_json, atomic_copy, read_json, sha256, event
from flow_base.outputs import artifact_directory, validate_tree, write_delivery_index
from . import worker
from .resources import catalogue
from .timeline import normalize_word_timelines


def inside(root, relative):
    root = Path(root).resolve()
    path = root / relative
    from flow_base.outputs import reject_links
    reject_links(path)
    path = path.resolve()
    if not path.is_relative_to(root):
        raise FlowError('RECOVERY_PATH', '恢復資料路徑超出工作區')
    return path


def verify_raw(path, vad, workflow, prepared):
    """Bind every row to audio, recognition settings and hash-verified checkpoints."""
    raw = read_json(path)
    resource = catalogue()[workflow.model]
    if (raw.get('vad_regions') != vad['regions'] or raw.get('duration') != vad['duration']
            or raw.get('model') != workflow.model or raw.get('model_revision') != resource.revision):
        raise FlowError('RECOVERY_MISMATCH', 'ASR 與原 VAD／模型不符')
    if vad.get('preprocessing') and (raw.get('preprocessing') != vad['preprocessing']
            or raw.get('skipped_silence') != vad['skipped_silence']
            or raw.get('vad_parameters') != vad['parameters']
            or raw.get('analysis_audio_sha256') != vad['analysis_audio_sha256']):
        raise FlowError('RECOVERY_MISMATCH', 'ASR 與人聲前處理或長靜音區間不符')
    options = workflow.options
    settings = {'model': workflow.model, 'model_revision': resource.revision, 'device': prepared['device'],
                'language': options.language, 'beam_size': options.beam_size,
                'prompt': options.initial_prompt, 'traditional_chinese': options.traditional_chinese}
    compatible = read_json(Path(__file__).with_name('recovery_compat.json'))['workers']
    hashes = {r['sha256'] for r in compatible} | {sha256(Path(worker.__file__))}
    rows, texts, metrics = [], {}, {}
    for region in vad['regions']:
        checkpoint = inside(path.parent, f"region_checkpoints/region_{region['id']:05d}.json")
        prior = read_json(checkpoint)
        result = prior['result']
        signatures = {worker.value_hash({'schema': 1, 'region': region, 'settings': settings, 'worker': h}) for h in hashes}
        if prior['signature'] not in signatures or prior['sha256'] != worker.value_hash(result):
            raise FlowError('RECOVERY_CHECKPOINT', '區段 checkpoint 指紋或內容不符', region_id=region['id'])
        if result['device'] != prepared['device']:
            raise FlowError('RECOVERY_CHECKPOINT', '區段裝置與原執行不符', region_id=region['id'])
        for segment in result['segments']:
            sid = len(rows)
            rows.append({'id': sid, **{k: segment[k] for k in ('start', 'end', 'text', 'words')}})
            texts[str(sid)] = segment['recognizer_text']
            metrics[str(sid)] = {**segment['metrics'], 'region_id': region['id']}
    if raw.get('segments') != rows or raw.get('recognizer_text') != texts or raw.get('recognition_metrics') != metrics:
        raise FlowError('RECOVERY_CHECKPOINT', 'ASR 組裝結果與 checkpoint 不符；不自動採用')
    if [r['region_id'] for r in raw.get('region_progress', [])] != [r['id'] for r in vad['regions']]:
        raise FlowError('RECOVERY_INCOMPLETE', '尚有未完成的辨識區段')
    return raw


def recover(runtime, adapter, output=None, raw_asr=None):
    action = getattr(runtime, 'action_context', None)
    if not action:
        raise FlowError('RUNTIME_UPDATE_REQUIRED', '恢復能力需要 flow_base 0.2.8 以上')
    folder = Path(action['folder']).resolve()
    if output is None:
        candidates = []
        for report_path in (folder / 'outputs').glob('*/report.json'):
            validate_tree(report_path.parent)
            report = read_json(report_path)
            if report.get('flow_id') == action['flow_id'] and report.get('status') == 'failed':
                candidates.append(report_path.parent)
        if len(candidates) != 1:
            raise FlowError('RECOVERY_SELECTION', '需要唯一的失敗工作；請在參數指定 output',
                            candidates=[str(p) for p in candidates])
        output = candidates[0]
    output = Path(output)
    if not output.is_absolute():
        output = runtime.home.parent / output
    validate_tree(output)
    output = output.resolve()
    failed = read_json(output / 'report.json')
    if failed.get('flow_id') != action['flow_id'] or failed.get('status') != 'failed':
        raise FlowError('RECOVERY_STATE', '僅接受目前流程的失敗成果，不覆寫已完成或其他流程的成果')
    with runtime.lock('output:' + str(output), timeout=1):
        if read_json(output / 'report.json') != failed:
            raise FlowError('RECOVERY_STATE', '成果狀態已變更，請重新檢查')
        artifacts = artifact_directory(output)
        adapter.check_output(runtime, None, artifacts)
        job = inside(runtime.home, f"jobs/{action['flow_id']}/{failed['job_id']}")
        snapshot = read_json(job / 'snapshot.json')
        if snapshot['flow_id'] != action['flow_id'] or snapshot['job_id'] != failed['job_id']:
            raise FlowError('RECOVERY_MISMATCH', '原執行快照與工作不符')
        workflow = adapter.configuration(snapshot['workflow'])
        # Historical checkpoints must retain their actual original prompt.
        old_prompt = snapshot['workflow'].get('options', {}).get('initial_prompt', '')
        workflow.options = workflow.options.model_copy(update={'initial_prompt': old_prompt})
        prepared = snapshot['prepared']
        location = snapshot['source']
        source = (runtime.home.parent / location['workspace_relative']).resolve() if 'workspace_relative' in location else Path(location['external_absolute'])
        if not source.is_file() or sha256(source) != snapshot['input_hash']:
            raise FlowError('RECOVERY_SOURCE', '原始影片遺失或已改變；不採用舊辨識')
        completed = {s['handler']: s for s in failed.get('steps', [])}
        if 'speech.vad' not in completed or 'media.extract' not in completed:
            raise FlowError('RECOVERY_INCOMPLETE', '沒有可驗證的抽音訊／VAD 完成紀錄')
        vad_path = runtime.cached(completed['speech.vad']['cache_key'])
        extracted = runtime.cached(completed['media.extract']['cache_key'])
        if not vad_path or not extracted or not adapter.artifact_valid(runtime, 'speech.vad', vad_path):
            raise FlowError('RECOVERY_AUDIO', '原音／VAD 快取缺失或損壞')
        from .subtitles import extracted_tracks
        audio, analysis, preprocessing = extracted_tracks(extracted)
        vad = read_json(vad_path)
        if preprocessing and (preprocessing != vad.get('preprocessing') or sha256(analysis) != vad.get('analysis_audio_sha256')):
            raise FlowError('RECOVERY_AUDIO', 'VAD 與處理後人聲指紋不符')
        if (inside(runtime.home, vad['source_audio']) != audio.resolve()
                or sha256(audio) != vad['source_audio_sha256'] or vad['source_sha256'] != sha256(audio)):
            raise FlowError('RECOVERY_AUDIO', 'VAD 與抽取原音指紋不符')
        # Existing 0.2.7 failures lack a pending-step pointer. Search only ASR
        # checkpoint directories, then require the full provenance proof above.
        choices = [inside(runtime.home, raw_asr)] if raw_asr else sorted((runtime.home / 'artifacts').glob('*/result.json'))
        matches = []
        for path in choices:
            if not (path.parent / 'region_checkpoints').is_dir():
                continue
            validate_tree(path.parent)
            try:
                raw = verify_raw(path, vad, workflow, prepared)
                matches.append((path, raw))
            except (FlowError, OSError, ValueError, KeyError, TypeError):
                if raw_asr:
                    raise
        if not matches:
            raise FlowError('RECOVERY_RAW_MISSING', '找不到通過全部 checkpoint 驗證的完整原始 ASR；未重新辨識')
        if len({worker.value_hash(raw) for _, raw in matches}) != 1:
            raise FlowError('RECOVERY_AMBIGUOUS', '找到多份不同辨識成果；請指定 raw_asr', candidates=[str(p) for p, _ in matches])
        raw_path, raw = matches[0]
        segments, issues = normalize_word_timelines(raw['segments'], raw['duration'])
        if not issues:
            raise FlowError('RECOVERY_NOT_APPLICABLE', '未發現可處理的字級時間異常；不掩蓋其他失敗')
        event('recovery_checkpoints_verified', regions=len(vad['regions']), raw_asr=str(raw_path),
              timing_issue_ids=[x['id'] for x in issues], asr_calls=0)
        from .adapter import configure_profile
        configure_profile(runtime, workflow)
        if not runtime.environment(workflow.profile)[1].is_file():
            raise FlowError('ENV_MISSING', '恢復需要原有處理環境；不會自動下載或重新辨識')
        work = job / 'recovery' / action['run_id']; work.mkdir(parents=True, exist_ok=True)
        atomic_copy(raw_path, work / 'raw_asr.json')
        data = {**raw, 'segments': segments, 'timing_issues': issues,
                'vad_artifact': vad_path.relative_to(runtime.home).as_posix(),
                'source_audio': vad['source_audio'], 'source_audio_sha256': vad['source_audio_sha256']}
        normalized = work / 'normalized.json'; atomic_json(normalized, data)
        ctx = Context(runtime, workflow, source, work, failed['job_id'], prepared, True,
                      Path(action['logdir']), action['flow_id'], action['run_id'])
        from .subtitles import review_step, export_step
        reviewed = review_step(ctx, workflow.steps[3], {'transcribe': normalized}, work / 'review.json')
        terminal = export_step(ctx, workflow.steps[4], {'review': reviewed}, work / 'export.json')
        atomic_copy(output / 'report.json', output / 'docs' / ('before-' + action['run_id'] + '.json'))
        report = adapter.publish_outputs(terminal, output, workflow) if failed.get('output_layout') == 'categorized-v1' else adapter.publish(terminal, output, workflow)
        report.update(flow_id=action['flow_id'], run_id=action['run_id'], job_id=failed['job_id'],
                      output=str(output), output_layout=failed.get('output_layout', 'flat-v1'),
                      snapshot=snapshot, steps=[*failed['steps'],
                        {'id':'transcribe','handler':'speech.transcribe','status':'recovered_from_verified_checkpoints'},
                        {'id':'review','handler':'text.review','status':'executed'},
                        {'id':'export','handler':'subtitle.export','status':'executed'}],
                      recovery={'source_run_id':failed['run_id'], 'asr_calls':0,
                                'verified_regions':len(vad['regions']), 'raw_sha256':sha256(raw_path),
                                'timing_issue_ids':[x['id'] for x in issues]})
        from flow_base import __version__
        from flow_base.usage import UsageMeter
        report.update(runtime=__version__, token_usage=UsageMeter().summary())
        atomic_json(output / 'report.json', report)
        write_delivery_index(output, report)
        event('recovery_finished', status=report['status'], output=str(output), asr_calls=0)
        return report

from __future__ import annotations
import json
from pathlib import Path
from flow_base import __version__
from flow_base.engine import Context, merge
from flow_base.runtime import FlowError, atomic_json, atomic_bytes, digest, read_json, require_space, sha256, memory_info, event, run_process, inspect_environment
from flow_base.resources import check_resource, ensure_resource, resource_path
from .contracts import Workflow, Step, subtitle_workflow
from .resources import SEPARATOR, SEPARATOR_PROFILE, catalogue
from .subtitles import HANDLERS

def resolve_environment(workflow, environment):
    """Domain policy consumes the host's snapshot; never probes hardware here."""
    profile, device = workflow.profile, workflow.options.device
    if profile not in {'auto', 'asr-cpu', 'asr-cuda'}:
        raise FlowError('PROFILE_INVALID', 'Unsupported execution profile')
    implied = {'asr-cpu': 'cpu', 'asr-cuda': 'cuda'}.get(profile)
    if implied and device != 'auto' and device != implied:
        raise FlowError('DEVICE_PROFILE_CONFLICT', 'profile 與 device 互相衝突；請明確選擇同一裝置或使用 auto。')
    requested = {'profile': profile, 'device': device}
    selected = device if device != 'auto' else implied
    gpu = environment.get('gpu', {})
    cuda = gpu.get('cuda', {})
    if selected:
        reason = 'explicit_setting'
    elif cuda.get('status') == 'disabled':
        selected, reason = 'cpu', 'cuda_disabled_by_environment'
    elif cuda.get('status') == 'available' or gpu.get('status') == 'detected':
        selected, reason = 'cuda', 'cuda_candidate_detected'
    elif cuda.get('status') == 'unknown' or gpu.get('status') == 'unknown':
        raise FlowError('HARDWARE_UNCERTAIN', '底座無法確定 CUDA 狀態；請查看偵測原因，或明確指定 device。', gpu=gpu)
    else:
        selected, reason = 'cpu', 'cuda_not_detected'
    concrete_profile = 'asr-cuda' if selected == 'cuda' else 'asr-cpu'
    selection = {'policy_version': 1, 'requested': requested,
                 'selected': {'profile': concrete_profile, 'device': selected},
                 'reason': reason, 'backend_verified': False, 'environment': environment}
    return workflow.model_copy(update={'profile': concrete_profile,
        'options': workflow.options.model_copy(update={'device': selected}), 'execution_selection': selection})


def ensure_resolved(runtime, workflow):
    if workflow.profile == 'auto' or workflow.options.device == 'auto':
        resolved = resolve_environment(workflow, inspect_environment(runtime.home))
        workflow.profile, workflow.options, workflow.execution_selection = resolved.profile, resolved.options, resolved.execution_selection
    return workflow


def configure_profile(runtime, workflow):
    ensure_resolved(runtime, workflow)
    for profile in (workflow.profile, SEPARATOR_PROFILE):
        runtime.register_profile(profile, Path(__file__).parent / 'profiles' / profile)
        runtime.workers[profile] = Path(__file__).with_name('worker.py')

def is_speech(workflow):
    return True

def handlers():
    return HANDLERS


def model_memory_bytes(workflow):
    # Conservative host-memory planning, not a guarantee of a model's measured peak.
    gib = {'whisper-tiny': 1, 'whisper-small': 3, 'whisper-large-v3': 6}[workflow.model]
    if workflow.profile == 'asr-cuda' and not workflow.options.allow_cpu_fallback:
        gib = min(gib, 4)
    return gib * 1024**3

def plan(runtime, workflow, source=None, output=None):
    configure_profile(runtime, workflow)
    env = workflow.execution_selection.get('environment') or inspect_environment(runtime.home, output)
    result = {"workflow": workflow.id, "environment": env, "actions": [], "blocked": [], "estimates": {},
              "recognition": {"engine": "faster-whisper", "model": workflow.model,
                              "revision": catalogue()[workflow.model].revision,
                              "device": workflow.options.device, "profile": workflow.profile,
                              "allow_cpu_fallback": workflow.options.allow_cpu_fallback,
                              "initial_prompt": workflow.options.initial_prompt,
                              "initial_prompt_enabled": workflow.options.initial_prompt_enabled},
              "audio_preprocessing": {"order": ["spectral_denoise", "demucs_vocals", "volume_normalize"],
                                      "model": SEPARATOR, "profile": SEPARATOR_PROFILE},
              "vad": {k: v for k, v in workflow.options.model_dump().items() if k.startswith("vad_")},
              "review": {"enabled": False, "llm_calls": 0, "llm_tokens": 0, "human_review_required": True},
              "pipeline": [s.handler for s in workflow.steps], "execution_selection": workflow.execution_selection}
    registry = handlers()
    for step in workflow.steps:
        if step.handler not in registry:
            result["blocked"].append({"code": "HANDLER_MISSING", "handler": step.handler})
    if source is not None and not source.is_file():
        result["blocked"].append({"code": "INPUT_MISSING", "path": str(source)})
    if workflow.profile != "core":
        supported = {("Windows", "AMD64"), ("Windows", "x86_64"), ("Linux", "x86_64"), ("Darwin", "arm64")}
        if is_speech(workflow) and ((env["os"], env["machine"]) not in supported or env["python"]["bits"] != 64):
            result["blocked"].append({"code": "PLATFORM_UNSUPPORTED", "os": env["os"], "machine": env["machine"]})
        _, python = runtime.environment(workflow.profile)
        if not python.is_file():
            result["actions"].append({"action": "prepare_environment", "profile": workflow.profile})
        resources = catalogue()
        download_bytes = 0
        for model_id in (workflow.model, SEPARATOR):
            status = check_resource(runtime, resources[model_id])
            if not status["ready"]:
                result["actions"].append({"action": "download_resource", **status})
                download_bytes += status["download_bytes"]
        estimated = download_bytes * 2 + workflow.options.min_free_bytes
        # Conservative configurable headroom, not a provider/package size guarantee.
        env_allowance = (4 if workflow.profile == "asr-cuda" else 1) * 1024**3 if not python.is_file() else 0
        if not runtime.environment(SEPARATOR_PROFILE)[1].is_file():
            result["actions"].append({"action": "prepare_environment", "profile": SEPARATOR_PROFILE})
            env_allowance += 2 * 1024**3
        estimated += env_allowance
        result["estimates"] = {"resource_download_bytes": download_bytes, "environment_allowance_bytes": env_allowance,
                               "model_memory_allowance_bytes": model_memory_bytes(workflow),
                               "home_required_bytes": estimated, "note": "installation peaks and media size vary; runtime rechecks space"}
        if env["free_bytes"]["home"] < estimated:
            result["blocked"].append({"code": "DISK_SPACE", "required": estimated})
        if not env["uv"]:
            result["blocked"].append({"code": "UV_MISSING"})
        available = env['memory']['available']
        if available is not None and available < model_memory_bytes(workflow):
            result['blocked'].append({'code': 'MEMORY_LOW', 'required': model_memory_bytes(workflow),
                                      'available': available, 'model': workflow.model,
                                      'message': '可用記憶體不足；請釋放記憶體或選擇合適設備，不會自動改用較小模型。'})
    result["ready_to_prepare"] = not result["blocked"]
    return result


def prepare(runtime, workflow, *, offline=False):
    runtime.initialize()
    ensure_resolved(runtime, workflow)
    p = plan(runtime, workflow)
    if p["blocked"]:
        raise FlowError("PREFLIGHT_BLOCKED", "Resolve preflight issues before running", blockers=p["blocked"])
    event('execution_selected', profile=workflow.profile, device=workflow.options.device,
          reason=workflow.execution_selection.get('reason', 'explicit_setting'))
    event("prepare_environment", profile=workflow.profile)
    runtime.prepare_profile(workflow.profile, offline=offline)
    event('check_dependencies', profile=workflow.profile)
    runtime.worker(workflow.profile, {'action': 'dependencies'}, timeout=120)
    if not is_speech(workflow):
        _, python = runtime.environment(workflow.profile)
        return {"ok": True, "profile": workflow.profile, "python_executable": str(python)}
    event("prepare_model", engine='faster-whisper', model=workflow.model, revision=catalogue()[workflow.model].revision)
    root = ensure_resource(runtime, catalogue()[workflow.model], offline=offline, reserve_bytes=workflow.options.min_free_bytes)
    event("prepare_environment", profile=SEPARATOR_PROFILE)
    runtime.prepare_profile(SEPARATOR_PROFILE, offline=offline)
    separator = catalogue()[SEPARATOR]
    event("prepare_model", engine="Demucs", model=separator.id, revision=separator.revision)
    ensure_resource(runtime, separator, offline=offline, reserve_bytes=workflow.options.min_free_bytes)
    return {"ok": True, "profile": workflow.profile, "model_path": str(root)}


def verify(runtime, workflow):
    configure_profile(runtime, workflow)
    if workflow.profile == "core":
        return {"ok": True, "profile": "core", "device": "cpu", "runtime": __version__}
    if not is_speech(workflow):
        _, python = runtime.environment(workflow.profile)
        result = run_process([python, "-c", "import importlib,json,sys; [importlib.import_module(n) for n in json.loads(sys.argv[1])]; print(json.dumps({'ok':True,'python':sys.version.split()[0]}))",
                              json.dumps(workflow.check_imports)], timeout=60)
        return {**json.loads(result), "profile": workflow.profile, "python_executable": str(python), "device": "cpu"}
    resource = catalogue()[workflow.model]
    if not check_resource(runtime, resource)["ready"]:
        raise FlowError("RESOURCE_MISSING", "Run prepare before verify")
    device = workflow.options.device
    if workflow.profile == "asr-cpu":
        device = "cpu"
    event("verify_backend", profile=workflow.profile, requested_device=device)
    result = runtime.worker(workflow.profile, {"action": "verify", "model_path": str(resource_path(runtime, resource)),
                                               "model": workflow.model, "model_revision": resource.revision,
                                               "device": device, "allow_cpu_fallback": workflow.options.allow_cpu_fallback,
                              "initial_prompt": workflow.options.initial_prompt,
                              "initial_prompt_enabled": workflow.options.initial_prompt_enabled}, timeout=300)
    separator = catalogue()[SEPARATOR]
    if not check_resource(runtime, separator)["ready"]:
        raise FlowError("RESOURCE_MISSING", "Demucs weights are missing or damaged", resource=SEPARATOR)
    weights = resource_path(runtime, separator) / separator.files[0].path
    result["separator"] = runtime.worker(SEPARATOR_PROFILE, {"action": "verify_separator", "weights": str(weights)}, timeout=300)
    result['profile'] = workflow.profile
    result['execution_selection'] = {**workflow.execution_selection, 'backend_verified': True,
                                    'actual_device': result['device'], 'fallback': result.get('fallback')}
    workflow.execution_selection = result['execution_selection']
    runtime.initialize()
    atomic_json(runtime.home / "verification" / (runtime.profile_key(workflow.profile) + ".json"), result)
    return result


def step_settings(workflow, handler, prepared):
    o = workflow.options.model_dump()
    if handler == "media.extract":
        return {"max_audio_seconds": o["max_audio_seconds"], "profile": workflow.profile,
                "separator": catalogue()[SEPARATOR].model_dump()}
    if handler == 'speech.vad':
        return {k: v for k, v in o.items() if k.startswith('vad_')}
    if handler == "speech.transcribe":
        keys = ["language", "traditional_chinese", "beam_size", "initial_prompt"]
        return {**{k: o[k] for k in keys}, "resource": catalogue()[workflow.model].model_dump(), "backend": prepared}
    if handler == "text.review":
        return {"llm": o["llm"], "glossary": o["glossary"]}
    if handler == "subtitle.export":
        return o
    return o


def demo(runtime, output: Path):
    """A labelled export fixture, never a model/QA/human-approval result."""
    from .subtitles import render
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FlowError('OUTPUT_EXISTS', 'Demo output must be empty')
    cues = [{'start':1.0,'end':3.2,'text':'這是字幕匯出示範。'}, {'start':8.0,'end':11.0,'text':'未執行辨識或真人核准。'}]
    atomic_bytes(output/'subtitles.srt', render(cues).encode())
    atomic_bytes(output/'subtitles.vtt', render(cues,True).encode())
    result = {'status':'REVIEW_REQUIRED','demonstration_only':True,'asr_executed':False,'output':str(output),'llm_calls':0,'llm_tokens':0}
    return result


from flow_base.engine import Adapter as BaseAdapter


class Adapter(BaseAdapter):
    handlers = HANDLERS

    def step_title(self, step):
        return {'media.extract':'抽取原音、去噪、Demucs 人聲分離與音量標準化','speech.vad':'偵測語音並跳過長靜音',
                'speech.transcribe':'逐區辨識與原片時間映射','text.review':'整理本機審閱資料',
                'subtitle.export':'字幕輸出、品質檢查與疑義原音'}.get(step.handler, step.id)

    def result_summary(self, report):
        label = {'REVIEW_REQUIRED':'字幕已產生，等待真人聽審；尚未核准',
                 'FAIL':'字幕已產生，但有品質問題；請開啟確認頁處理',
                 'PASS':'字幕已通過真人核准'}.get(report.get('status'),'請查看本流程結果狀態')
        if report.get('output'):
            from flow_base.outputs import artifact_directory
            artifacts = artifact_directory(Path(report['output']))
            label += '\n請先開啟字幕確認頁：' + str(artifacts/'review.html')
            if report.get('summary', {}).get('delivery_zip'):
                label += '\n分享包：' + str(artifacts/report['summary']['delivery_zip'])
        return {'label':label}

    def describe(self, workflow):
        return [f'辨識引擎：faster-whisper；模型：{workflow.model}；裝置：{workflow.options.device}',
                f'辨識提示 initial_prompt：{workflow.options.initial_prompt or "（已明確停用）"}',
                '流程：抽音訊 → 保存 VAD 區段 → 逐區辨識／續跑 → 本機 QA、疑義原音與 SRT/VTT → 真人聽審核准',
                '本機 runner：LLM API 呼叫 0 次；未經真人確認維持 REVIEW_REQUIRED。',
                '模型不會自動降級；CPU 執行 large-v3 可能耗時較長。']

    def configuration(self, overrides):
        workflow = Workflow.model_validate(merge(subtitle_workflow().model_dump(), overrides))
        if workflow.profile not in {'auto', 'asr-cpu', 'asr-cuda'} or workflow.profile_project:
            raise FlowError('PROFILE_INVALID', 'This task supports its bundled CPU/CUDA dependency profiles only')
        return workflow

    def resolve_environment(self, workflow, environment):
        return resolve_environment(workflow, environment)

    def plan(self, runtime, workflow, source=None, output=None):
        return plan(runtime, workflow, source, output)

    def prepare(self, runtime, workflow, offline=False):
        return prepare(runtime, workflow, offline=offline)

    def verify(self, runtime, workflow):
        return verify(runtime, workflow)

    def pre_run(self, runtime, workflow, source, offline):
        if workflow.options.llm.enabled or workflow.options.llm.max_calls:
            raise FlowError('LOCAL_LLM_FORBIDDEN', '本字幕 runner 禁止 LLM API 呼叫')

    def inspect_input(self, runtime, workflow, source):
        media = runtime.worker(workflow.profile, {'action': 'probe', 'input': str(source)}, timeout=60)
        duration = media['duration']
        if duration is not None and duration > workflow.options.max_audio_seconds:
            raise FlowError('AUDIO_LIMIT', 'Media exceeds configured duration limit', duration=duration)
        duration = duration or workflow.options.max_audio_seconds
        # Original PCM16, temporary vocal float32 and normalized PCM16 can coexist.
        require_space(runtime.home, int(duration * 16000 * 8) + workflow.options.min_free_bytes)
        available = memory_info()['available']
        required = int(duration * 16000 * 12) + model_memory_bytes(workflow)
        if available is not None and available < required:
            raise FlowError('MEMORY_LOW', 'Estimated ASR peak exceeds available memory; split input or use a device with more memory. Model remains unchanged.', required=required, available=available)

    def step_settings(self, workflow, handler, prepared):
        # Absolute interpreter location does not change recognition results after relocation.
        backend = {k: v for k, v in prepared.items() if k not in {'python_executable', 'model_path', 'execution_selection'}}
        return step_settings(workflow, handler, backend)

    def profile_key(self, runtime, workflow):
        configure_profile(runtime, workflow)
        return runtime.profile_key(workflow.profile)

    def minimum_space(self, workflow):
        return workflow.options.min_free_bytes

    def job_scoped(self, handler):
        return handler == 'text.review'

    def check_output(self, runtime, workflow, output):
        if any((output/name).exists() for name in ('edits.json', 'timing_edits.json', 'approval.json')):
            raise FlowError('HUMAN_REVIEW_PRESENT', '已有人工修訂或核准；請使用本流程 review-build 能力，或改用新的輸出位置。')

    def invoke_action(self, runtime, name, parameters):
        from pydantic import Field
        from flow_base.contracts import Contract
        class RecoveryParameters(Contract):
            output: str | None = None
            raw_asr: str | None = None
        if name == 'recover-run':
            from .recovery import recover
            return recover(runtime, self, **RecoveryParameters.model_validate(parameters).model_dump())
        class BuildParameters(Contract):
            output: str
            force_rebuild: bool = False
        class ImportParameters(Contract):
            output: str
            csv_path: str
        class ApprovalParameters(Contract):
            output: str
            reviewer: str = Field(min_length=1)
            reviewed_issues: int = Field(ge=0)
            confirm_listened: bool
        class PacketParameters(Contract):
            output: str
        schemas = {'review-build': BuildParameters, 'review-approve': ApprovalParameters, 'review-packet': PacketParameters, 'review-import': ImportParameters}
        if name not in schemas:
            raise FlowError('TASK_ACTION_UNKNOWN', 'Unknown subtitle action')
        params = schemas[name].model_validate(parameters).model_dump()
        workspace = getattr(runtime, 'workspace_root', runtime.home.parent)
        output = (workspace / params.pop('output')).resolve()
        from flow_base.outputs import artifact_directory, validate_tree
        validate_tree(output)
        artifacts = artifact_directory(output)
        if name == 'review-packet':
            packet = read_json(artifacts/'review_packet.json')
            rows = packet['segments']
            count = sum(len(r['text']) + len(r['context_before']) + len(r['context_after']) for r in rows)
            if len(rows) > 20 or count > 3000 or any(len(r[k]) > 60 for r in rows for k in ('context_before', 'context_after')):
                raise FlowError('REVIEW_PACKET_LIMIT', 'Packet exceeds local review policy')
            return {'packet': str(artifacts/'review_packet.json'), 'segments':len(rows), 'characters':count,
                    'llm_calls':0, 'llm_tokens':0, 'instruction':'僅在使用者明確要求 AI 協助時讀取一次；先檢查遮罩後是否仍含個資，不能自動核准。'}
        if name == 'review-approve' and not params['confirm_listened']:
            raise FlowError('HUMAN_APPROVAL_REQUIRED', '必須由真人明確確認聽審；不能由 runner 或模型代替。')
        if name == 'review-import':
            params['csv_path'] = str((workspace / params['csv_path']).resolve())
        operation = {'review-approve':'approve', 'review-import':'import'}.get(name, 'build')
        return self.review_operation(runtime, output, operation, **params)

    def artifact_valid(self, runtime, handler, artifact):
        # The host already verifies the primary artifact hash. Only JSON bundles
        # need domain-specific sidecar checks; decoded audio is a binary WAV.
        if handler == 'media.extract' and artifact.suffix == '.json':
            from .subtitles import extracted_tracks
            try:
                extracted_tracks(artifact)
                return True
            except (OSError, KeyError, TypeError, ValueError, FlowError):
                return False
        if handler not in {'speech.vad', 'subtitle.export'}:
            return True
        try:
            data = read_json(artifact)
            if handler == 'speech.vad':
                return all((artifact.parent / r['audio_file']).is_file() and
                           (artifact.parent / r['audio_file']).resolve().is_relative_to(artifact.parent.resolve()) and
                           sha256(artifact.parent / r['audio_file']) == r['sha256'] for r in data['regions'])
            if handler == 'subtitle.export':
                folder = artifact.parent / 'bundle'
                return all((folder/name).is_file() and (folder/name).resolve().is_relative_to(folder.resolve()) and
                           sha256(folder/name) == expected for name,expected in data['files'].items())
            return True
        except (OSError, KeyError, ValueError):
            return False

    def publish(self, terminal, output, workflow):
        from flow_base.runtime import atomic_copy
        export = read_json(terminal)
        folder = terminal.parent / 'bundle'
        # Never overwrite a human revision or an approval by rerunning recognition.
        if any((output/name).exists() for name in ('edits.json','timing_edits.json','approval.json')):
            raise FlowError('HUMAN_REVIEW_PRESENT', 'Use review-build for human edits or choose a new output; existing revisions are preserved')
        for name, expected in export['files'].items():
            path = (folder/name).resolve()
            destination = (output/name).resolve()
            if not path.is_relative_to(folder.resolve()) or not destination.is_relative_to(output.resolve()) or sha256(path) != expected:
                raise FlowError('HANDLER_CONTRACT', 'Invalid review bundle file')
            atomic_copy(path, destination)
        return {**export['report'], 'execution_selection': workflow.execution_selection}

    def review_operation(self, runtime, output, operation, **kwargs):
        from flow_base.outputs import artifact_directory, validate_tree, write_delivery_index
        validate_tree(output)
        artifacts = artifact_directory(output)
        context = read_json(artifacts/'flow_context.json')
        config = self.configuration({'profile':context['profile'], 'model':context['model'],
                                     'options':{'device':'cuda' if context['profile']=='asr-cuda' else 'cpu'}})
        configure_profile(runtime, config)
        runtime.prepare_profile(config.profile)
        audio = (runtime.home/context['source_audio']).resolve()
        if not audio.is_relative_to(runtime.home) or not audio.is_file() or sha256(audio) != context['source_audio_sha256']:
            raise FlowError('AUDIO_INTEGRITY', 'Original listening audio missing or changed; restore the workspace cache')
        with runtime.lock('output:' + str(output.resolve()), timeout=1):
            validate_tree(output)
            def human_state():
                return {name: sha256(artifacts/name) if (artifacts/name).is_file() else None
                        for name in ('edits.json', 'approval.json')}
            before_human_state = human_state()
            try:
                summary = runtime.worker(config.profile, {'action':'review_operation','operation':operation,
                    'output_dir':str(artifacts), 'source_audio':str(audio), **kwargs}, timeout=600)
            except Exception:
                if operation == 'import' and human_state() != before_human_state:
                    report = read_json(output/'report.json')
                    report.update(status='failed', review_rebuild_required=True,
                                  review_message='填答匯入或重建失敗；請查看錯誤，不能視為已完成或已核准。')
                    atomic_json(output/'report.json',report)
                    write_delivery_index(output, report)
                raise
            report = read_json(output/'report.json')
            report.update(status=summary['decision'], summary=summary, cues=summary.get('cue_count', report.get('cues')),
                          issue_count=summary.get('review_count', report.get('issue_count')), llm_calls=0, llm_tokens=0, review_rebuild_required=False)
            report.pop('review_message', None)
            atomic_json(output/'report.json',report)
            write_delivery_index(output, report)
            runtime.update_run(report['run_id'],summary['decision'],finished=True)
            atomic_json(runtime.logs/report['flow_id']/report['run_id']/'report.json',report)
            return summary

    def demo(self, runtime, output):
        return demo(runtime, output)

    def resources(self):
        return catalogue()

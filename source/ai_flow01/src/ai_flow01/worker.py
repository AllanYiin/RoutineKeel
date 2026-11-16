"""Standalone isolated ASR worker; no dependency on the runtime package.

Invoked by an explicit Python executable with one JSON request path.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import wave
from pathlib import Path


def save(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_name(p.name + ".part")
    temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(p)


def configure_cuda_libraries():
    """Re-exec before native imports so Linux's loader sees bundled CUDA libs."""
    if os.environ.get("FLOW_BASE_CUDA_LIBS_READY"):
        return
    import site
    paths = []
    for root in site.getsitepackages():
        for part in ("cublas", "cudnn"):
            p = Path(root) / "nvidia" / part / "lib"
            if p.is_dir():
                paths.append(str(p))
    if paths and sys.platform.startswith("linux"):
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = os.pathsep.join(paths + ([env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else []))
        env["FLOW_BASE_CUDA_LIBS_READY"] = "1"
        os.execve(sys.executable, [sys.executable, *sys.argv], env)


def extract(req):
    import av
    source, dest = req["input"], Path(req["output"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(dest.name + ".part")
    limit = int(req["max_audio_seconds"] * 16000)
    written = 0
    last_progress = 0.0
    with av.open(source) as container, wave.open(str(temp), "wb") as out:
        if not container.streams.audio:
            raise ValueError("NO_AUDIO_STREAM: input contains no audio")
        stream = container.streams.audio[0]
        total_seconds = float(container.duration) / 1000000 if container.duration else None
        origin = float(container.start_time or 0) / 1000000
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)

        def write_frame(frame):
            nonlocal written, last_progress
            # Audio PTS are mapped onto the media timeline; gaps remain silence.
            position = written if frame.pts is None else max(0, round((float(frame.pts * frame.time_base) - origin) * 16000))
            data = frame.to_ndarray().tobytes()
            samples = len(data) // 2
            if position + samples > limit:
                raise ValueError("AUDIO_LIMIT: maximum audio duration exceeded")
            if position > written:
                gap = position - written
                while gap:
                    n = min(gap, 16000)
                    out.writeframesraw(b"\x00\x00" * n)
                    gap -= n
                written = position
            elif position < written:
                trim = min(written - position, samples)
                data = data[trim * 2:]
            out.writeframesraw(data)
            written += len(data) // 2
            now = time.monotonic()
            if total_seconds and written / 16000 <= total_seconds and now-last_progress >= 1:
                print(json.dumps({'event':'progress','completed':written/16000,'total':total_seconds,
                                  'unit':'秒音訊','label':'抽取音訊'}),flush=True)
                last_progress = now

        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                write_frame(converted)
        for converted in resampler.resample(None):
            write_frame(converted)
    if not written:
        temp.unlink(missing_ok=True)
        raise ValueError("EMPTY_AUDIO: no decoded samples")
    temp.replace(dest)
    print(json.dumps({'event':'progress','completed':written/16000,'total':written/16000,
                      'unit':'秒音訊','label':'抽取音訊'}),flush=True)
    return {"audio": str(dest), "duration": written / 16000, "sample_rate": 16000}


def probe(req):
    import av
    with av.open(req["input"]) as container:
        if not container.streams.audio:
            raise ValueError("NO_AUDIO_STREAM: input contains no audio")
        stream = container.streams.audio[0]
        duration = float(container.duration) / 1000000 if container.duration else None
        if duration is None and stream.duration is not None:
            duration = float(stream.duration * stream.time_base)
        return {"duration": duration, "audio_streams": len(container.streams.audio)}


def dependencies(req):
    """Fail before downloading GBs of model data; also exercises Windows native imports."""
    configure_cuda_libraries()
    import importlib
    import importlib.metadata
    for name in ('pkg_resources', 'ctranslate2', 'faster_whisper', 'onnxruntime', 'av', 'opencc'):
        importlib.import_module(name)
    return {'ok': True, 'python_executable': sys.executable,
            'versions': {name: importlib.metadata.version(name) for name in
                         ('setuptools', 'ctranslate2', 'faster-whisper', 'onnxruntime', 'av')}}


def load_model(req):
    configure_cuda_libraries()
    import ctranslate2
    from faster_whisper import WhisperModel
    requested = req.get("device", "cpu")
    fallback = None
    candidate = requested
    if requested == "auto":
        try:
            candidate = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception as e:
            if not req.get("allow_cpu_fallback", False):
                raise
            candidate, fallback = "cpu", type(e).__name__
        if candidate == 'cpu' and not req.get('allow_cpu_fallback', False):
            raise RuntimeError('CUDA_UNAVAILABLE: no usable CUDA device; explicitly select CPU or allow_cpu_fallback')
    try:
        model = WhisperModel(req["model_path"], device=candidate,
                             compute_type="float16" if candidate == "cuda" else "int8", local_files_only=True)
    except Exception as e:
        if candidate != "cuda" or not req.get("allow_cpu_fallback", False):
            raise
        fallback = type(e).__name__
        candidate = "cpu"
        model = WhisperModel(req["model_path"], device="cpu", compute_type="int8", local_files_only=True)
    return model, candidate, fallback


def verify(req):
    import importlib.metadata
    import numpy as np
    model, device, fallback = load_model(req)
    # Execute encoder/decoder, not just a device enumeration or model import.
    segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1, vad_filter=False)
    list(segments)
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    get_speech_timestamps(np.zeros(16000, dtype=np.float32), VadOptions())
    return {"ok": True, "device": device, "fallback": fallback, "python": sys.version.split()[0],
            "engine": "faster-whisper", "model": req.get('model'), "model_revision": req.get('model_revision'),
            "compute_type": 'float16' if device == 'cuda' else 'int8',
            "versions": {name: importlib.metadata.version(name) for name in ["faster-whisper", "ctranslate2", "av", "onnxruntime"]}}


def merge_speech_regions(regions, sample_rate=16000, max_gap_seconds=1.0, max_seconds=30.0):
    """Keep short natural pauses, but never concatenate distant speech on one timeline."""
    merged = []
    for region in regions:
        start, end = int(region['start']), int(region['end'])
        if end <= start:
            continue
        if merged and start - merged[-1]['end'] <= max_gap_seconds * sample_rate and end - merged[-1]['start'] <= max_seconds * sample_rate:
            merged[-1]['end'] = max(merged[-1]['end'], end)
        else:
            merged.append({'start': start, 'end': end})
    return merged


def file_hash(path):
    import hashlib
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def value_hash(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_pcm(path):
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError('ASR requires 16kHz mono PCM16')
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)


def vad(req):
    import numpy as np
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    pcm = read_pcm(req['input'])
    audio = pcm.astype(np.float32) / 32768.0
    detected = get_speech_timestamps(audio, VadOptions(
        threshold=req.get('vad_threshold', 0.4), min_speech_duration_ms=req.get('vad_min_speech_ms', 150),
        min_silence_duration_ms=req.get('vad_min_silence_ms', 550),
        speech_pad_ms=req.get('vad_speech_pad_ms', 250), max_speech_duration_s=req.get('vad_max_speech_seconds', 28)))
    regions = detected
    target = Path(req['output'])
    folder = target.parent / 'audio'
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, region in enumerate(regions):
        name = f'region_{index:05d}.wav'
        path = folder / name
        temp = path.with_suffix('.part')
        with wave.open(str(temp), 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
            out.writeframes(pcm[region['start']:region['end']].tobytes())
        temp.replace(path)
        entries.append({'id': index, 'start_sample': region['start'], 'end_sample': region['end'],
                        'start': region['start'] / 16000, 'end': region['end'] / 16000,
                        'audio_file': 'audio/' + name, 'sha256': file_hash(path)})
    save(target, {'schema_version': 1, 'duration': len(pcm) / 16000, 'sample_rate': 16000,
                  'source_sha256': file_hash(req['input']), 'regions': entries})
    return {'regions': len(entries), 'duration': len(pcm) / 16000}


def transcribe(req):
    import numpy as np
    from opencc import OpenCC
    manifest_path = Path(req['vad_path'])
    plan = json.loads(manifest_path.read_text(encoding='utf-8'))
    target = Path(req['output'])
    checkpoints = target.parent / 'region_checkpoints'
    checkpoints.mkdir(parents=True, exist_ok=True)
    convert = OpenCC('s2twp').convert if req.get('traditional_chinese', True) else lambda text: text
    settings = {k: req.get(k) for k in ('model', 'model_revision', 'device', 'language', 'beam_size', 'prompt', 'traditional_chinese')}
    model = None
    device, fallback = req.get('device', 'cpu'), None
    rows, original_text, metrics, progress = [], {}, {}, []
    detected_language = req.get('language')
    if plan['regions']:
        print(json.dumps({'event':'progress','completed':0,'total':len(plan['regions']),'unit':'區段','label':'語音辨識'}),flush=True)
    for region in plan['regions']:
        audio_path = (manifest_path.parent / region['audio_file']).resolve()
        if not audio_path.is_relative_to(manifest_path.parent.resolve()) or file_hash(audio_path) != region['sha256']:
            raise ValueError('VAD_AUDIO_INTEGRITY: speech region is missing or changed')
        signature = value_hash({'schema': 1, 'region': region, 'settings': settings, 'worker': file_hash(__file__)})
        checkpoint = checkpoints / f"region_{region['id']:05d}.json"
        result = None
        try:
            prior = json.loads(checkpoint.read_text(encoding='utf-8'))
            if prior['signature'] == signature and prior['sha256'] == value_hash(prior['result']):
                result = prior['result']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        status = 'cached' if result is not None else 'executed'
        if result is None:
            if model is None:
                model, device, fallback = load_model(req)
            audio = read_pcm(audio_path).astype(np.float32) / 32768.0
            segments, info = model.transcribe(audio, language=req.get('language'), beam_size=req.get('beam_size', 5),
                                             vad_filter=False, word_timestamps=True, condition_on_previous_text=False,
                                             initial_prompt=req.get('prompt') or None)
            items = []
            for segment in segments:
                if segment.end <= segment.start or not segment.text.strip():
                    continue
                offset = region['start']
                items.append({'start': round(segment.start + offset, 6), 'end': round(segment.end + offset, 6),
                              'text': convert(segment.text.strip()), 'recognizer_text': segment.text.strip(),
                              'words': [{'start': round(w.start + offset, 6), 'end': round(w.end + offset, 6), 'word': convert(w.word)}
                                        for w in (segment.words or []) if w.end >= w.start],
                              'metrics': {name: getattr(segment, name, None) for name in ('avg_logprob', 'no_speech_prob', 'compression_ratio')}})
            result = {'segments': items, 'language': info.language, 'device': device, 'fallback': fallback}
            save(checkpoint, {'signature': signature, 'sha256': value_hash(result), 'result': result})
        detected_language = detected_language or result['language']
        device, fallback = result['device'], result['fallback']
        for segment in result['segments']:
            sid = len(rows)
            original_text[str(sid)] = segment['recognizer_text']
            metrics[str(sid)] = {**segment['metrics'], 'region_id': region['id']}
            rows.append({'id': sid, **{k: segment[k] for k in ('start', 'end', 'text', 'words')}})
        progress.append({'region_id': region['id'], 'status': status})
        save(target.with_suffix('.progress.json'), {'regions': progress, 'total': len(plan['regions'])})
        print(json.dumps({'event':'progress','completed':len(progress),'total':len(plan['regions']),
                          'unit':'區段','label':'語音辨識','region_status':status}),flush=True)
    data = {'segments': rows, 'recognizer_text': original_text, 'recognition_metrics': metrics,
            'language': detected_language, 'duration': plan['duration'],
            'duration_after_vad': sum(r['end'] - r['start'] for r in plan['regions']),
            'vad_regions': plan['regions'], 'region_progress': progress, 'device': device, 'fallback': fallback,
            'engine': 'faster-whisper', 'model': req.get('model'), 'model_revision': req.get('model_revision')}
    save(target, data)
    return {'segments': len(rows), 'device': device, 'regions': len(progress)}


def domain_core():
    import importlib.util
    spec = importlib.util.spec_from_file_location('portable_core', Path(__file__).with_name('portable_core.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def qa_bundle(req):
    core = domain_core()
    data = json.loads(Path(req['input']).read_text(encoding='utf-8'))
    out = Path(req['output_dir'])
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for segment in data['segments']:
        metric = data.get('recognition_metrics', {}).get(str(segment['id']), {})
        rows.append({**{k: segment[k] for k in ('id', 'start', 'end', 'text')},
                     'avg_logprob': metric.get('avg_logprob'), 'no_speech_prob': metric.get('no_speech_prob'),
                     'words': [{'start': w['start'], 'end': w['end'], 'text': w['word']} for w in segment['words']]})
    config = req['config']
    core.enforce_local_policy(config)
    save(out / 'transcript.json', rows)
    save(out / 'asr_details.json', data)
    save(out / 'timing_issues.json', data.get('timing_issues', []))
    save(out / 'review_config.json', config)
    save(out / 'manifest.json', {'version': 'flow-subtitles-0.3.4', 'source': '', 'source_title': req.get('source_title', '課程影片'),
                                'duration': data['duration'], 'model': data['model'], 'model_revision': data['model_revision'],
                                'config_sha256': value_hash(config), 'source_sha256': req['source_sha256']})
    # portable-srt hashes JSON with its default ensure_ascii setting.
    import hashlib
    manifest = core.read_json(out / 'manifest.json')
    manifest['config_sha256'] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    save(out / 'manifest.json', manifest)
    save(out / 'flow_context.json', req['flow_context'])
    save(out / 'vad_regions.json', {'regions': data.get('vad_regions', []), 'sample_rate':16000})
    save(out / 'region_progress.json', {'regions': data.get('region_progress', [])})
    core.build(out, config, source_audio=req['source_audio'])
    finish_review_files(out)
    return core.read_json(out / 'summary.json')


def finish_review_files(out):
    core = domain_core()
    import shutil
    shutil.copyfile(out / 'subtitles.zh-TW.srt', out / 'subtitles.srt')
    if not (out / 'original.srt').exists():
        shutil.copyfile(out / 'subtitles.zh-TW.srt', out / 'original.srt')
    cues = core.read_json(out / 'cues.json')
    parts = ['WEBVTT\n']
    for index, cue in enumerate(cues, 1):
        parts.append(f"{index}\n{core.stamp(cue['start']).replace(',', '.')} --> {core.stamp(cue['end']).replace(',', '.')}\n{cue['text']}\n")
    (out / 'subtitles.vtt').write_text('\n'.join(parts), encoding='utf-8')
    core.human_review().package(out)


def review_operation(req):
    core = domain_core()
    out = Path(req['output_dir'])
    config = core.read_json(out / 'review_config.json')
    core.enforce_local_policy(config)
    if req['operation'] == 'build':
        core.build(out, config, force_rebuild=req.get('force_rebuild', False), source_audio=req['source_audio'])
    elif req['operation'] == 'import':
        core.human_review().import_answers(out, req['csv_path'])
        core.build(out, config, force_rebuild=True, source_audio=req['source_audio'])
    elif req['operation'] == 'approve':
        if not req.get('confirm_listened'):
            raise ValueError('Human listening confirmation is required')
        core.approve(out, config, req['reviewer'], req['reviewed_issues'], source_audio=req['source_audio'])
    else:
        raise ValueError('Unknown review operation')
    finish_review_files(out)
    return core.read_json(out / 'summary.json')


def main():
    req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    action = req["action"]
    handlers = {"extract": extract, "probe": probe, "verify": verify, "transcribe": transcribe, "dependencies": dependencies, "vad": vad, "qa_bundle": qa_bundle, "review_operation": review_operation}
    if action not in handlers:
        raise ValueError("unknown worker action")
    result = handlers[action](req)
    if req.get("response_path"):
        save(req["response_path"], result)
    else:
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

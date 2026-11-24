"""The subtitle adapter: domain logic separated from generic runtime services."""
from __future__ import annotations

import re

from flow_base.runtime import FlowError, atomic_json, read_json, sha256

from .contracts import Segment
from .resources import SEPARATOR, SEPARATOR_PROFILE, catalogue, resource_path
from .review import review


def validate_segments(rows, duration=None):
    segments = [Segment.model_validate(row).model_dump() for row in rows]
    last, seen = 0, set()
    for s in segments:
        if s["id"] in seen or s["start"] < last - 0.05:
            raise FlowError("TRANSCRIPT_TIMELINE", "Transcript IDs/timing must be unique and ordered")
        if duration is not None and s["end"] > duration + 0.2:
            raise FlowError("TRANSCRIPT_TIMELINE", "Transcript exceeds source duration")
        seen.add(s["id"])
        last = s["end"]
    return segments


def extract(ctx, step, dependencies, target):
    original = target.parent / 'original.wav'
    vocals = target.parent / 'vocals.wav'
    options = ctx.workflow.options
    request = {'input': str(ctx.source), 'max_audio_seconds': options.max_audio_seconds,
               'audio_preprocessing': options.audio_preprocessing}
    ctx.runtime.worker(ctx.workflow.profile, {**request, 'action': 'extract', 'output': str(original)},
                       timeout=options.worker_timeout_seconds, log=ctx.logdir / 'extract.log')
    model = catalogue()[SEPARATOR]
    weights = resource_path(ctx.runtime, model) / model.files[0].path
    preprocessing = ctx.runtime.worker(SEPARATOR_PROFILE, {**request, 'action': 'prepare_audio',
        'weights': str(weights), 'original': str(original), 'output': str(vocals)},
        timeout=options.worker_timeout_seconds, log=ctx.logdir / 'demucs.log')
    preprocessing['model_sha256'] = model.files[0].sha256
    atomic_json(target, {'schema_version': 2, 'source_audio_sha256': sha256(original),
                         'analysis_audio_sha256': sha256(vocals), 'preprocessing': preprocessing})
    return target


def extracted_tracks(artifact):
    """Resolve the human-review and ASR inputs, including historical mono WAV caches."""
    if artifact.suffix == '.wav':
        return artifact, artifact, None
    record = read_json(artifact)
    original, analysis = artifact.parent / 'original.wav', artifact.parent / 'vocals.wav'
    for track, field in ((original, 'source_audio_sha256'), (analysis, 'analysis_audio_sha256')):
        if track.is_symlink() or not track.is_file() or sha256(track) != record[field]:
            raise FlowError('AUDIO_INTEGRITY', '抽取原音或處理後人聲遺失／已改變', file=track.name)
    return original, analysis, record['preprocessing']


def vad(ctx, step, dependencies, target):
    original, analysis, preprocessing = extracted_tracks(next(iter(dependencies.values())))
    options = ctx.workflow.options.model_dump()
    req = {'action': 'vad', 'input': str(analysis), 'output': str(target),
           **{k: v for k, v in options.items() if k.startswith('vad_')}}
    ctx.runtime.worker(ctx.workflow.profile, req, timeout=ctx.workflow.options.worker_timeout_seconds, log=ctx.logdir/'vad.log')
    data = read_json(target)
    data['source_audio'] = original.relative_to(ctx.runtime.home).as_posix()
    data['source_sha256'] = data['source_audio_sha256'] = sha256(original)
    data['preprocessing'] = preprocessing
    atomic_json(target, data)
    return target


def transcribe(ctx, step, dependencies, target):
    options = ctx.workflow.options
    vad_plan = next(iter(dependencies.values()))
    resource = catalogue()[ctx.workflow.model]
    request = {"action": "transcribe", "vad_path": str(vad_plan), "output": str(target),
               "model": ctx.workflow.model, "model_revision": resource.revision,
               "model_path": str(resource_path(ctx.runtime, resource)), "device": ctx.prepared["device"],
               "allow_cpu_fallback": False, "language": options.language, "traditional_chinese": options.traditional_chinese, "beam_size": options.beam_size,
               "vad_min_silence_ms": options.vad_min_silence_ms, "vad_speech_pad_ms": options.vad_speech_pad_ms,
               "prompt": options.initial_prompt}
    ctx.runtime.worker(ctx.workflow.profile, request, timeout=options.worker_timeout_seconds, log=ctx.logdir / "asr.log")
    data = read_json(target)
    # Preserve recognizer evidence before applying any alignment fallback.
    from flow_base.runtime import atomic_copy, event

    from .timeline import normalize_word_timelines
    raw = target.with_suffix('.raw.json')
    atomic_copy(target, raw)
    data['vad_artifact'] = vad_plan.relative_to(ctx.runtime.home).as_posix()
    data['source_audio'] = read_json(vad_plan)['source_audio']
    data['source_audio_sha256'] = read_json(vad_plan)['source_audio_sha256']
    data['segments'], data['timing_issues'] = normalize_word_timelines(data['segments'], data['duration'])
    if data['timing_issues']:
        event('word_timeline_review_required', count=len(data['timing_issues']),
              segment_ids=[r['id'] for r in data['timing_issues']], raw_asr=str(raw))
    atomic_json(target, data)
    return target


def review_step(ctx, step, dependencies, target):
    source = read_json(next(iter(dependencies.values())))
    original = validate_segments(source["segments"], source.get("duration"))
    revised, report = review(ctx.runtime, ctx.job_id, original, ctx.workflow.options.llm,
                             ctx.workflow.options.glossary, offline=ctx.offline)
    data = {**source, "segments": validate_segments(revised, source.get("duration")), "original_segments": original, "review": report}
    atomic_json(target, data)
    return target


def split_lines(text, width):
    text = re.sub(r"\s+", " ", text).strip().replace("-->", "→")
    lines = []
    while len(text) > width:
        # Prefer a nearby space for Latin text, but always make progress for CJK.
        split = text.rfind(" ", max(1, width // 2), width + 1)
        split = split if split > 0 else width
        lines.append(text[:split].strip())
        text = text[split:].lstrip()
    if text:
        lines.append(text)
    return lines


def cues_from_segments(segments, width=30, max_seconds=12):
    """Compatibility helper for export fixtures; production QA uses portable_core."""
    cues, warnings = [], []
    for s in segments:
        words = s.get("words") or []
        groups = []
        # Only use word timing if it actually describes the current text.
        normalize = lambda t: re.sub(r"\s+", "", t)
        aligned = words and normalize("".join(w["word"] for w in words)) == normalize(s["text"])
        if aligned:
            group = []
            for word in words:
                text = "".join(w["word"] for w in group + [word]).strip()
                if group and (len(split_lines(text, width)) > 2 or word["end"] - group[0]["start"] > max_seconds
                              or word['start'] - group[-1]['end'] > 1.0):
                    groups.append((group[0]["start"], group[-1]["end"], "".join(w["word"] for w in group).strip()))
                    group = []
                group.append(word)
            if group:
                groups.append((group[0]["start"], group[-1]["end"], "".join(w["word"] for w in group).strip()))
        else:
            groups = [(s["start"], s["end"], s["text"])]
        for start, end, text in groups:
            if end <= start or not text.strip():
                warnings.append({"id": s["id"], "reason": "zero_duration_or_empty_cue"})
                continue
            lines = split_lines(text, width)
            if len(lines) > 2 or end - start > max_seconds:
                warnings.append({"id": s["id"], "reason": "needs_manual_resegmentation", "start": start, "end": end})
            # Preserve actual timing. Never invent word timing for an LLM rewrite.
            cues.append({"start": start, "end": end, "text": "\n".join(lines), "segment_id": s["id"]})
    return cues, warnings


def timestamp(seconds, separator):
    ms = round(seconds * 1000)
    hours, rest = divmod(ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    sec, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{sec:02}{separator}{millis:03}"


def render(cues, vtt=False):
    parts = ["WEBVTT\n"] if vtt else []
    previous = 0
    for index, c in enumerate(cues, 1):
        start, end = round(c["start"] * 1000), round(c["end"] * 1000)
        if start < previous or end <= start:
            raise FlowError("SUBTITLE_TIMELINE", "Invalid subtitle timing after millisecond rounding")
        previous = end
        sep = "." if vtt else ","
        parts.append(f"{index}\n{timestamp(c['start'], sep)} --> {timestamp(c['end'], sep)}\n{c['text']}\n")
    return "\n".join(parts) + ("\n" if parts else "")


def export_step(ctx, step, dependencies, target):
    source = next(iter(dependencies.values()))
    data = read_json(source)
    o = ctx.workflow.options
    config = {'model': 'large-v3' if ctx.workflow.model == 'whisper-large-v3' else ctx.workflow.model,
              'language': o.language, 'traditional_chinese': o.traditional_chinese,
              'max_line_width': o.max_line_chars, 'max_lines': 2,
              'max_cps': o.max_cps, 'min_cue_seconds': o.min_cue_seconds, 'max_cue_seconds': o.max_cue_seconds,
              'low_logprob_threshold': o.low_logprob_threshold, 'spot_check_count': o.spot_check_count,
              'llm': o.llm.model_dump()}
    folder = target.parent / 'bundle'
    audio = (ctx.runtime.home / data['source_audio']).resolve()
    if not audio.is_relative_to(ctx.runtime.home) or sha256(audio) != data['source_audio_sha256']:
        raise FlowError('AUDIO_INTEGRITY', 'Source audio missing or changed')
    context = {'source_audio': data['source_audio'], 'source_audio_sha256': data['source_audio_sha256'],
               'profile': ctx.workflow.profile, 'model': ctx.workflow.model}
    summary = ctx.runtime.worker(ctx.workflow.profile, {'action': 'qa_bundle', 'input': str(source), 'output_dir': str(folder),
        'config': config, 'source_title': ctx.source.stem, 'source_audio': str(audio), 'source_sha256': sha256(ctx.source), 'flow_context': context},
        timeout=o.worker_timeout_seconds, log=ctx.logdir/'qa.log')
    files = {p.relative_to(folder).as_posix(): sha256(p) for p in folder.rglob('*') if p.is_file()}
    report = {'status': summary['decision'], 'cues': summary['cue_count'], 'issue_count': summary['review_count'],
              'engine': 'faster-whisper', 'model': ctx.workflow.model, 'model_revision': data['model_revision'],
              'device': data['device'], 'profile': ctx.workflow.profile,
              'execution_selection': ctx.prepared.get('execution_selection', ctx.workflow.execution_selection),
              'duration': data['duration'], 'llm_calls': 0, 'llm_tokens': 0,
              'qa': 'QA.json', 'listening_review': 'review.html', 'summary': summary}
    if data.get('preprocessing'):
        report.update(preprocessing=data['preprocessing'], vad_parameters=data['vad_parameters'],
                      skipped_silence_count=len(data['skipped_silence']),
                      skipped_silence_seconds=sum(r['end_sample'] - r['start_sample'] for r in data['skipped_silence']) / 16000)
    atomic_json(target, {'bundle': 'bundle', 'files': files, 'report': report})
    return target


HANDLERS = {"media.extract": extract, "speech.vad": vad, "speech.transcribe": transcribe, "text.review": review_step,
            "subtitle.export": export_step}

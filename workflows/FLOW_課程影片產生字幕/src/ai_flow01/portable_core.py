"""Adapted domain core from user-provided portable-srt 0.1.0 cli.py.
flow_base supplies generic runtime services; this package owns ASR and review.
See PROVENANCE.md for scope and upstream hash.
"""
from __future__ import annotations
import csv, hashlib, html, json, math, re, shutil, unicodedata, wave
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path


def enforce_local_policy(config):
    policy = config['llm']
    if policy.get('enabled') or policy.get('max_calls') != 0:
        raise ValueError('LOCAL_LLM_FORBIDDEN: runner has a hard zero-call budget')
    if not 0 <= policy['max_segments'] <= 20 or not 0 <= policy['max_total_chars'] <= 3000 or not 0 <= policy['context_chars'] <= 60:
        raise ValueError('Review packet exceeds the 20 segment / 3000 character / 60 context limit')


def make_review_audio(out, manifest, issues, source_audio=None, rows=None):
    if source_audio is None or not Path(source_audio).is_file():
        return {}
    paths = {}
    windows = {}
    source_rows = {int(row['id']): row for row in rows} if rows is not None else None
    clips = out / 'review_clips'
    clips.mkdir(exist_ok=True)
    with wave.open(str(source_audio), 'rb') as source:
        rate = source.getframerate()
        total = source.getnframes()
        padding = round(1.5 * rate)
        for issue in issues:
            sid = int(issue['id'])
            if sid in paths:
                continue
            interval = source_rows[sid] if source_rows is not None else issue
            core_start = round(float(interval['start']) * rate)
            core_end = round(float(interval['end']) * rate)
            start = max(0, core_start - padding)
            end = min(total, core_end + padding)
            if not 0 <= core_start < core_end <= total or end <= start:
                raise ValueError('REVIEW_AUDIO_TIMELINE: source interval is outside original audio')
            source.setpos(start)
            frames = source.readframes(end - start)
            if len(frames) != (end - start) * source.getnchannels() * source.getsampwidth():
                raise ValueError('REVIEW_AUDIO_TIMELINE: incomplete original PCM interval')
            if not frames:
                continue
            clip = clips / f'{sid:06}.wav'
            with wave.open(str(clip), 'wb') as target:
                target.setnchannels(source.getnchannels()); target.setsampwidth(source.getsampwidth()); target.setframerate(rate)
                target.writeframes(frames)
            paths[sid] = f'review_clips/{clip.name}'
            windows[str(sid)] = {'path': paths[sid], 'start_sample': start, 'end_sample': end,
                                 'segment_start_sample': core_start, 'segment_end_sample': core_end,
                                 'frames': end - start, 'sha256': sha256(clip)}
    write_json(out / 'review_audio.json', {'timebase': 'original_media_samples', 'sample_rate': rate,
                                         'source_frames': total, 'padding_seconds': 1.5, 'clips': windows})
    return paths

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def write_json(path: Path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def width(text: str) -> float:
    return sum(1 for char in text if not unicodedata.combining(char))

def stamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hour, milliseconds = divmod(milliseconds, 3_600_000)
    minute, milliseconds = divmod(milliseconds, 60_000)
    second, milliseconds = divmod(milliseconds, 1000)
    return f"{hour:02}:{minute:02}:{second:02},{milliseconds:03}"

def traditional(text: str) -> str:
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise RuntimeError("OpenCC missing; install requirements.txt") from exc
    if not hasattr(traditional, "converter"):
        traditional.converter = OpenCC("s2twp")
    return traditional.converter.convert(text)

def normalize(text: str, traditional_chinese: bool = True) -> str:
    if not traditional_chinese:
        return re.sub(r"\s+", " ", text.strip())
    text = traditional(text.strip())
    text = text.replace(",", "，").replace("?", "？").replace("!", "！")
    text = re.sub(r"\s+", " ", text)
    return text

def split_text(text: str, max_width: float) -> list[str]:
    """Prefer punctuation; split long spans only when the display limit demands it."""
    chunks = []
    limit = max_width * 2 - 2
    for sentence in re.findall(r".*?[。！？；]|.+$", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        while width(sentence) > limit:
            choices = [m.end() for m in re.finditer(r"[，、：]", sentence)
                       if width(sentence[:m.end()]) <= limit]
            if choices:
                cut = max(choices)
            else:
                cut = max(i for i in range(1, len(sentence))
                          if width(sentence[:i]) <= limit)
            chunks.append(sentence[:cut])
            sentence = sentence[cut:]
        if sentence:
            chunks.append(sentence)
    return chunks

def wrap(text: str, max_width: float) -> str:
    if width(text) <= max_width:
        return text
    candidates = [i for i in range(1, len(text))
                  if width(text[:i]) <= max_width and width(text[i:]) <= max_width]
    if not candidates:
        raise ValueError(f"Cannot fit two lines: {text}")
    punctuation = [i for i in candidates if text[i - 1] in "，、：；。！？"]
    pool = punctuation or candidates
    cut = min(pool, key=lambda i: abs(width(text[:i]) - width(text[i:])))
    return text[:cut] + "\n" + text[cut:]

def edited_rows(rows: list[dict], edits: dict, timing_edits: dict | None = None, traditional_chinese: bool = True) -> list[dict]:
    timing_edits = timing_edits or {}
    by_id = {str(row["id"]) for row in rows}
    unknown = (set(edits) | set(timing_edits)) - by_id
    if unknown:
        raise ValueError(f"Unknown edit IDs: {sorted(unknown)}")
    result = []
    for row in rows:
        clone = dict(row)
        clone["text"] = normalize(edits.get(str(row["id"]), row["text"]), traditional_chinese)
        if str(row["id"]) in edits and clone["text"] != normalize(row["text"], traditional_chinese):
            clone["words"] = []
        if str(row["id"]) in timing_edits:
            timing = timing_edits[str(row["id"])]
            if not isinstance(timing, dict) or set(timing) != {"start", "end"}:
                raise ValueError(f"Invalid timing edit for {row['id']}")
            start, end = float(timing["start"]), float(timing["end"])
            if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end:
                raise ValueError(f"Invalid timing range for {row['id']}")
            clone["start"], clone["end"] = start, end
            clone["words"] = []
        result.append(clone)
    return result

def display_chunks(row, config, cursor, end, cues, issues):
    source = row["text"]
    chunks = split_text(source, config["max_line_width"])
    timed_words = row.get("words") or []
    word_chars = "".join(normalize(word["text"], config.get("traditional_chinese", True)) for word in timed_words)
    word_timing_ok = word_chars.replace(" ", "") == source.replace(" ", "")
    word_positions = []
    if word_timing_ok:
        chars = 0
        for word in timed_words:
            chars += len(normalize(word["text"], config.get("traditional_chinese", True)).replace(" ", ""))
            word_positions.append((chars, float(word["end"])))
    consumed = 0
    for index, chunk in enumerate(chunks):
        remaining = sum(max(width(part), 1) for part in chunks[index:])
        if index == len(chunks) - 1:
            chunk_end = end
        elif word_timing_ok:
            consumed += len(chunk.replace(" ", ""))
            candidates = [time for position, time in word_positions if position >= consumed and time > cursor]
            chunk_end = min(end, candidates[0]) if candidates else cursor
            if chunk_end <= cursor:
                chunk_end = cursor + (end - cursor) * max(width(chunk), 1) / remaining
                word_timing_ok = False
        else:
            chunk_end = cursor + (end - cursor) * max(width(chunk), 1) / remaining
        text = wrap(chunk, config["max_line_width"])
        cues.append({"start": round(cursor, 3), "end": round(chunk_end, 3),
                     "text": text, "source_id": row["id"]})
        cursor = chunk_end
    if len(chunks) > 1 and not word_timing_ok:
        issues.append({"id": row["id"], "reason": "approximate_split_timing",
                       "text": source, "start": row["start"], "end": row["end"]})


def wrap_retained(text, line_width):
    # A continuous passage can exceed both display limits. Keep its single timecode,
    # preserve every character, and let QA report the excess lines.
    lines, start, used = [], 0, 0
    if width(text) <= 2 * line_width:
        return wrap(text, line_width)
    for i, char in enumerate(text):
        char_width = width(char)
        if used + char_width > line_width:
            lines.append(text[start:i])
            start, used = i, 0
        used += char_width
    lines.append(text[start:])
    return "\n".join(lines)


def cues_from(rows: list[dict], config: dict, duration: float, *, find_pauses=None, pause_log=None) -> tuple[list[dict], list[dict]]:
    cues, issues = [], []
    limit = math.floor(config["max_cue_seconds"] * 1000)
    for row in rows:
        source = row["text"]
        if not source:
            continue
        cursor = max(0.0, float(row["start"]))
        end = min(duration, float(row["end"]))
        if end <= cursor:
            issues.append({"id": row["id"], "reason": "invalid_timing", "text": source,
                           "start": cursor, "end": end})
            continue
        first, last = round(cursor * 1000), round(end * 1000)
        if last - first > limit:
            gaps = find_pauses(first, last) if find_pauses else None
            parts, cuts, retained = domain_module("pause_splitting").partition(
                row, first, last, limit, config["max_line_width"] * config["max_lines"], gaps,
                lambda word: normalize(word, config.get("traditional_chinese", True)), width)
            if pause_log is not None:
                pause_log.append({"source_id": row["id"], "start_ms": first, "end_ms": last,
                                  "measured_pauses_ms": gaps, "cuts": cuts, "retained": retained})
            for start_ms, end_ms, text in parts:
                cues.append({"start": start_ms / 1000, "end": end_ms / 1000,
                             "text": wrap_retained(text, config["max_line_width"]), "source_id": row["id"]})
            if any(c["text_mapping"] == "estimated_text" for c in cuts):
                issues.append({"id": row["id"], "reason": "pause_text_estimated", "text": source,
                               "start": cursor, "end": end})
            for fragment in retained:
                issues.append({"id": row["id"], "reason": fragment["reason"], "text": source,
                               "start": fragment["start_ms"] / 1000, "end": fragment["end_ms"] / 1000})
        else:
            display_chunks(row, config, cursor, end, cues, issues)
        if row.get("avg_logprob") is not None and row["avg_logprob"] < config["low_logprob_threshold"]:
            issues.append({"id": row["id"], "reason": "low_asr_confidence",
                           "text": source, "start": row["start"], "end": row["end"]})
        if row.get("no_speech_prob") is not None and row["no_speech_prob"] > 0.6:
            issues.append({"id": row["id"], "reason": "possible_silence_hallucination",
                           "text": source, "start": row["start"], "end": row["end"]})
    cues.sort(key=lambda cue: (cue["start"], cue["end"]))
    return cues, issues

def validate(cues: list[dict], config: dict, duration: float) -> list[dict]:
    findings = []
    previous = None
    for index, cue in enumerate(cues, 1):
        lines = cue["text"].splitlines()
        span = cue["end"] - cue["start"]
        count = sum(width(line) for line in lines)
        if cue["start"] < 0 or cue["end"] > duration or span <= 0:
            findings.append({"cue": index, "reason": "invalid_time"})
        if previous and cue["start"] < previous["end"] - 0.001:
            findings.append({"cue": index, "reason": "overlap"})
        if len(lines) > config["max_lines"] or any(width(line) > config["max_line_width"] for line in lines):
            findings.append({"cue": index, "reason": "line_limit"})
        if span > 0 and count / span > config["max_cps"]:
            findings.append({"cue": index, "reason": "reading_speed",
                             "severity": "review" if count / span <= config["max_cps"] * 1.2
                             else "error"})
        if span < config["min_cue_seconds"]:
            findings.append({"cue": index, "reason": "short_duration", "severity": "review"})
        elif round(cue["end"] * 1000) - round(cue["start"] * 1000) > math.floor(config["max_cue_seconds"] * 1000):
            findings.append({"cue": index, "reason": "long_duration", "severity": "error"})
        previous = cue
    return findings

def ease_short_cues(cues: list[dict], config: dict, duration: float, skipped_silence=(), fixed_ids=()) -> list[dict]:
    """Use nearby free display time; never overlap speech or hold across a long gap."""
    from bisect import bisect_right
    gap_ends = [gap["end"] for gap in skipped_silence]
    adjusted = [dict(cue) for cue in cues]
    for index, cue in enumerate(adjusted):
        if cue.get("source_id") in fixed_ids:
            continue
        readable_width = sum(width(line) for line in cue["text"].splitlines())
        target = max(config["min_cue_seconds"], readable_width / config["max_cps"])
        target = min(target, config.get("max_cue_seconds", math.inf))
        missing = target - (cue["end"] - cue["start"])
        if missing <= 0:
            continue
        gap_index = bisect_right(gap_ends, cue["start"] + 0.0005)
        floor = gap_ends[gap_index - 1] if gap_index else 0
        ceiling = skipped_silence[gap_index]["start"] if gap_index < len(gap_ends) else duration
        if cue["end"] > ceiling + 0.0005:
            continue  # Keep an explicitly retimed cue as written.
        next_start = adjusted[index + 1]["start"] if index + 1 < len(adjusted) else duration
        postroll = min(missing, max(0, min(next_start, ceiling) - cue["end"]), 1.5)
        cue["end"] = round(cue["end"] + postroll, 3)
        missing -= postroll
        if missing > 0:
            previous_end = adjusted[index - 1]["end"] if index else 0
            preroll = min(missing, max(0, cue["start"] - max(previous_end, floor)), 0.4)
            cue["start"] = round(cue["start"] - preroll, 3)
    return adjusted

def spot_check_issues(cues: list[dict], rows: list[dict], existing: list[dict], config: dict) -> list[dict]:
    if len(cues) < 100:
        return []
    blocked = {issue["id"] for issue in existing}
    candidates = [cue for cue in cues if cue["source_id"] not in blocked]
    count = min(int(config.get("spot_check_count", 10)), len(candidates))
    by_id = {row["id"]: row for row in rows}
    selected, seen = [], set()
    for index in range(count):
        cue = candidates[min(len(candidates) - 1, (index + 1) * len(candidates) // (count + 1))]
        source_id = cue["source_id"]
        if source_id in seen:
            continue
        seen.add(source_id)
        row = by_id[source_id]
        selected.append({"id": source_id, "reason": "spot_check_unflagged",
                         "text": row["text"], "start": row["start"], "end": row["end"]})
    return selected

def review_packet(rows: list[dict], issues: list[dict], config: dict) -> tuple[list[dict], bool]:
    def redact(value: str) -> str:
        value = re.sub(r"(?i)\b[\w.+-]+@[\w.-]+\.[A-Z]{2,}\b", "[EMAIL]", value)
        value = re.sub(r"(?i)\b(?:https?://|www\.)\S+", "[URL]", value)
        value = re.sub(r"(?i)\b(?:sk-|api[_-]?key[:= ]+)[A-Za-z0-9_-]{12,}\b",
                       "[SECRET]", value)
        value = re.sub(r"(?<!\d)\d{9,}(?!\d)", "[LONG_NUMBER]", value)
        return value

    policy = config["llm"]
    unique = {}
    for issue in issues:
        if issue["reason"] != "low_asr_confidence":
            continue
        unique.setdefault(issue["id"], set()).add(issue["reason"])
    by_id = {row["id"]: row for row in rows}
    positions = {row["id"]: index for index, row in enumerate(rows)}
    packet, used = [], 0
    for source_id, reasons in unique.items():
        row = by_id[source_id]
        text = redact(row["text"])
        position = positions[source_id]
        context_limit = policy["context_chars"]
        before = redact(rows[position - 1]["text"])[-context_limit:] if position and context_limit else ""
        after = redact(rows[position + 1]["text"])[:context_limit] if position + 1 < len(rows) and context_limit else ""
        size = len(text) + len(before) + len(after)
        if len(packet) >= policy["max_segments"] or used + size > policy["max_total_chars"]:
            break
        packet.append({"id": source_id, "text": text, "reasons": sorted(reasons),
                       "start": row["start"], "end": row["end"],
                       "context_before": before, "context_after": after,
                       "redacted": text != row["text"]})
        used += size
    return packet, len(packet) < len(unique)

def build(out: Path, config: dict, force_rebuild: bool = False, source_audio=None):
    enforce_local_policy(config)
    manifest = read_json(out / "manifest.json")
    source = Path(manifest.get("source", ""))
    if source.is_file() and manifest.get("source_sha256") and sha256(source) != manifest["source_sha256"]:
        raise RuntimeError("Source video changed after transcription")
    if manifest.get("config_sha256"):
        current = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        if current != manifest["config_sha256"]:
            raise RuntimeError("Config changed after transcription; use a new run")
    rows = read_json(out / "transcript.json")
    edits_path = out / "edits.json"
    edits = read_json(edits_path) if edits_path.exists() else {}
    timing_path = out / "timing_edits.json"
    timing_edits = read_json(timing_path) if timing_path.exists() else {}
    rows = edited_rows(rows, edits, timing_edits, config.get('traditional_chinese', True))
    duration = float(manifest["duration"])
    vad_path = out / "vad_regions.json"
    vad = read_json(vad_path) if vad_path.is_file() else {}
    splitter = domain_module("pause_splitting")
    threshold = vad.get("parameters", {}).get("threshold", 0.4)
    audio, track = None, "not_needed"
    limit = math.floor(config["max_cue_seconds"] * 1000)
    if any(row["text"] and round(min(duration, row["end"]) * 1000) - round(max(0, row["start"]) * 1000) > limit for row in rows):
        audio, track = splitter.analysis_track(source_audio, vad, sha256)
    pause_rows = []
    cues, issues = cues_from(rows, config, duration, pause_log=pause_rows,
                            find_pauses=lambda a, b: splitter.acoustic_pauses(audio, a, b, threshold))
    timing_issue_path = out / 'timing_issues.json'
    if timing_issue_path.exists():
        by_id = {row['id']: row for row in rows}
        for finding in read_json(timing_issue_path):
            row = by_id[finding['id']]
            issues.append({'id': row['id'], 'reason': 'invalid_word_timeline',
                           'text': row['text'], 'start': row['start'], 'end': row['end']})
    fixed_ids = {entry["source_id"] for entry in pause_rows}
    cues = ease_short_cues(cues, config, duration, vad.get("skipped_silence", []), fixed_ids)
    findings = validate(cues, config, float(manifest["duration"]))
    row_by_id = {row["id"]: row for row in rows}
    for finding in findings:
        cue = cues[finding["cue"] - 1]
        row = row_by_id[cue["source_id"]]
        issues.append({"id": row["id"], "reason": f"qa_{finding['reason']}",
                       "text": row["text"], "start": row["start"], "end": row["end"]})
    issues.extend(spot_check_issues(cues, rows, issues, config))
    srt = "\n\n".join(
        f"{index}\n{stamp(cue['start'])} --> {stamp(cue['end'])}\n{cue['text']}"
        for index, cue in enumerate(cues, 1)
    ) + ("\n" if cues else "")
    srt_path = out / "subtitles.zh-TW.srt"
    if srt_path.exists() and srt_path.read_text(encoding="utf-8") != srt:
        if not force_rebuild:
            raise RuntimeError("SRT would change; pass --force-rebuild after reviewing the edits")
        versions = out / "versions"
        versions.mkdir(exist_ok=True)
        old_hash = sha256(srt_path)
        old_version = versions / f"{old_hash}.srt"
        if not old_version.exists():
            old_version.write_bytes(srt_path.read_bytes())
    srt_path.write_text(srt, encoding="utf-8")
    write_json(out / "cues.json", cues)
    write_json(out / "pause_splits.json", {"audio_track": track, "threshold": threshold,
               "minimum_pause_ms": splitter.MIN_PAUSE_MS, "max_cue_seconds": config["max_cue_seconds"],
               "segments": pause_rows})
    packet, capped = review_packet(rows, issues, config)
    write_json(out / "review_packet.json", {
        "purpose": "Only flagged segments; no automatic LLM call", "segments": packet,
        "capped": capped, "segment_count": len(packet),
        "character_count": sum(len(item["text"]) + len(item["context_before"]) +
                               len(item["context_after"]) for item in packet)
    })
    audio_paths = make_review_audio(out, manifest, issues, source_audio, rows=rows)
    # Keep human notes when the exact issue is unchanged; archive old tables when changed.
    review_path = out / 'review.csv'
    previous_review = review_path.read_bytes() if review_path.exists() else None
    confirmed = {}
    issue_keys = ('id', 'start', 'end', 'reason', 'text')
    if previous_review:
        with review_path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                confirmed[tuple(row.get(k, '') for k in issue_keys)] = row.get('confirmed_text', '')
    with review_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "start", "end", "reason", "text", "confirmed_text"])
        writer.writeheader()
        writer.writerows([
            {"id": issue["id"], "start": issue["start"], "end": issue["end"],
             "reason": issue["reason"], "text": issue["text"],
             "confirmed_text": confirmed.get(tuple(str(issue[k]) for k in issue_keys), '')}
            for issue in issues
        ])
    if previous_review is not None and previous_review != review_path.read_bytes():
        versions = out / 'versions'
        versions.mkdir(exist_ok=True)
        (versions / (hashlib.sha256(previous_review).hexdigest() + '.review.csv')).write_bytes(previous_review)
    approval_path = out / "approval.json"
    approval = read_json(approval_path) if approval_path.exists() else {}
    valid_approval = (
        approval.get("srt_sha256") == sha256(srt_path)
        and approval.get("review_sha256") == sha256(out / "review.csv")
        and approval.get("reviewed_issues") == len(issues)
    )
    if len(audio_paths) < len({issue["id"] for issue in issues}):
        findings.append({"reason": "review_audio_missing", "severity": "error"})
    hard_findings = [finding for finding in findings if finding.get("severity") != "review"]
    decision = "FAIL" if hard_findings else ("PASS" if valid_approval else "REVIEW_REQUIRED")
    observed_width = max((width(line) for cue in cues for line in cue["text"].splitlines()), default=0)
    observed_lines = max((len(cue["text"].splitlines()) for cue in cues), default=0)
    observed_cps = max((
        sum(width(line) for line in cue["text"].splitlines()) /
        max(cue["end"] - cue["start"], 0.001) for cue in cues
    ), default=0)
    write_json(out / "QA.json", {
        "decision": decision, "reason": "Human speech review confirmed" if valid_approval
        else "Human speech review is required before final approval",
        "cue_count": len(cues), "issue_count": len(issues), "findings": findings,
        "hard_finding_count": len(hard_findings),
        "max_lines_observed": observed_lines,
        "max_line_width_observed": round(observed_width, 2),
        "max_cps_observed": round(observed_cps, 2),
        "review_packet_capped": capped, "llm_calls": 0, "llm_tokens": 0,
        "review_audio_count": len(audio_paths),
        "srt_sha256": sha256(srt_path)
    })
    write_json(out / "summary.json", {
        "decision": decision, "cue_count": len(cues),
        "review_count": len(issues), "format_finding_count": len(hard_findings),
        "review_finding_count": len(findings) - len(hard_findings),
        "first_findings": findings[:5], "review_audio_count": len(audio_paths),
        "llm_calls": 0, "llm_tokens": 0,
        "srt": "subtitles.zh-TW.srt", "review": "review.html", "qa": "QA.json"
    })
    delivery = human_review().render(out, rows, issues, audio_paths, read_json(out / 'QA.json'))
    summary = read_json(out / 'summary.json')
    summary.update(review='review.html', review_table=delivery['table'],
                   transcript=delivery['transcript'], delivery_zip=delivery['archive'],
                   review_segments=len({issue['id'] for issue in issues}))
    summary.update(pause_split_count=sum(len(r["cuts"]) for r in pause_rows),
                   pause_retained_count=sum(len(r["retained"]) for r in pause_rows))
    write_json(out / 'summary.json', summary)
    print(f"{decision}: {len(cues)} cues, {len(issues)} review items, "
          f"{len(hard_findings)} hard findings, {len(findings) - len(hard_findings)} review findings")

def approve(out: Path, config: dict, reviewer: str, reviewed_issues: int, source_audio=None):
    if not reviewer.strip():
        raise ValueError("Reviewer name is required")
    build(out, config, source_audio=source_audio)
    qa = read_json(out / "QA.json")
    if qa.get("hard_finding_count", len(qa["findings"])):
        raise ValueError("Resolve QA findings before approval")
    if reviewed_issues != qa["issue_count"]:
        raise ValueError("Reviewed issue count does not match QA.json")
    write_json(out / "approval.json", {
        "reviewer": reviewer.strip(), "reviewed_issues": reviewed_issues,
        "confirmed_at_utc": datetime.now(timezone.utc).isoformat(),
        "srt_sha256": sha256(out / "subtitles.zh-TW.srt"),
        "review_sha256": sha256(out / "review.csv"),
    })
    build(out, config, source_audio=source_audio)


@lru_cache(maxsize=2)
def domain_module(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location("flow_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def human_review():
    return domain_module("human_review")

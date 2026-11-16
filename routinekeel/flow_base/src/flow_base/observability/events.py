"""Durable per-Run JSONL flight recorder with bounded rotation and scan-first reads."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from ..diagnostics import redact
from ..runtime import FlowError, atomic_json
from .trace import current_span

ATTRIBUTE_KEYS = frozenset({'profile', 'executable_id', 'command_fingerprint', 'cache_key',
                            'artifact_hash', 'status', 'code', 'resource_id', 'duration_ms',
                            'bytes', 'output_layout', 'step_id', 'result_status', 'reason',
                            'child_pid', 'exit_code'})
_PROCESS_MONOTONIC_ORIGIN = time.monotonic()


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class EventRecorder:
    def __init__(self, runtime, run_id, logdir, *, max_bytes=1_000_000, max_segments=8):
        if max_bytes < 1024 or max_segments < 1:
            raise ValueError('Event retention requires at least 1024 bytes and one segment')
        self.runtime = runtime
        self.run_id = run_id
        self.logdir = Path(logdir)
        self.max_bytes = max_bytes
        self.max_segments = max_segments
        self.started = _PROCESS_MONOTONIC_ORIGIN

    def _files(self):
        segments = sorted(self.logdir.glob('flight.[0-9][0-9][0-9][0-9][0-9][0-9].jsonl'))
        current = self.logdir / 'flight.jsonl'
        return segments + ([current] if current.exists() else [])

    def _index(self, payload, file_name, offset):
        if self.runtime is None:
            return
        try:
            with closing(self.runtime.connect()) as db:
                with db:
                    db.execute('INSERT INTO event_index(event_id,run_id,step_attempt_id,event_type,timestamp_utc,file_path,offset_bytes) VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET file_path=excluded.file_path,offset_bytes=excluded.offset_bytes',
                               (payload['event_id'], self.run_id, payload.get('step_attempt_id'),
                                payload['name'], payload['timestamp_utc'], file_name, offset))
        except sqlite3.Error:
            # The fsynced JSONL is authoritative; rebuild_index repairs this.
            pass

    def _write(self, payload, path):
        line = (json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        offset = path.stat().st_size if path.exists() else 0
        with path.open('ab') as output:
            output.write(line)
            output.flush()
            os.fsync(output.fileno())
        self._index(payload, path.name, offset)

    def _rotate(self, incoming):
        current = self.logdir / 'flight.jsonl'
        if not current.exists() or current.stat().st_size + incoming <= self.max_bytes:
            return
        segments = sorted(self.logdir.glob('flight.[0-9][0-9][0-9][0-9][0-9][0-9].jsonl'))
        last = max((int(path.stem.split('.')[1]) for path in segments), default=0)
        rotated = self.logdir / f'flight.{last+1:06}.jsonl'
        os.replace(current, rotated)
        if self.runtime is not None:
            try:
                with closing(self.runtime.connect()) as db:
                    with db:
                        db.execute('UPDATE event_index SET file_path=? WHERE run_id=? AND file_path=?',
                                   (rotated.name, self.run_id, current.name))
            except sqlite3.Error:
                pass
        segments.append(rotated)
        dropped = 0
        while len(segments) > self.max_segments:
            oldest = segments.pop(0)
            oldest.unlink()
            dropped += 1
            if self.runtime is not None:
                try:
                    with closing(self.runtime.connect()) as db:
                        with db:
                            db.execute('DELETE FROM event_index WHERE run_id=? AND file_path=?', (self.run_id, oldest.name))
                except sqlite3.Error:
                    pass
        manifest_path = self.logdir / 'flight_manifest.json'
        was_truncated = False
        if manifest_path.exists():
            try:
                was_truncated = bool(json.loads(manifest_path.read_text(encoding='utf-8')).get('truncated'))
            except (OSError, ValueError):
                pass
        if dropped:
            marker = self._envelope('recorder.truncated', 'WARNING', {'reason': 'retention_limit'})
            marker_size = len((json.dumps(marker, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
            if marker_size + incoming <= self.max_bytes:
                self._write(marker, current)
        try:
            atomic_json(manifest_path,
                        {'schema_version': '1.0', 'current': current.name,
                         'segments': [path.name for path in segments], 'truncated': was_truncated or bool(dropped)})
        except OSError:
            pass

    def _envelope(self, name, severity, attributes, *, step_attempt_id=None, span_id=None, parent_span_id=None):
        from ..core.run_context import current
        run = current(self.runtime) if self.runtime is not None else current()
        if run is not None and run.run_id != self.run_id:
            run = None
        active_span = current_span()
        safe = {key: redact(value) for key, value in attributes.items() if key in ATTRIBUTE_KEYS}
        safe = {key: value[:256] if isinstance(value, str) else value for key, value in safe.items()}
        return {'schema_version': '1.0', 'event_id': 'evt_' + uuid.uuid4().hex,
                'timestamp_utc': _utc(), 'elapsed_ms': int((time.monotonic()-self.started)*1000),
                'run_id': self.run_id,
                'step_attempt_id': step_attempt_id or (run.attempt_id if run else None),
                'span_id': span_id or (active_span.span_id if active_span else (run.span_id if run else None)),
                'parent_span_id': parent_span_id or (active_span.parent_span_id if active_span else (run.parent_span_id if run else None)),
                'process_id': os.getpid(), 'severity': severity, 'name': name,
                'event': name, 'attributes': safe}

    def record(self, name, *, severity='INFO', attributes=None, step_attempt_id=None,
               span_id=None, parent_span_id=None):
        payload = self._envelope(name, severity, attributes or {}, step_attempt_id=step_attempt_id,
                                 span_id=span_id, parent_span_id=parent_span_id)
        self.append(payload)
        return payload

    def append_legacy(self, payload):
        safe = redact(payload)
        self.logdir.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(safe, ensure_ascii=False) + '\n').encode('utf-8')
        if len(line) > self.max_bytes:
            raise FlowError('EVENT_TOO_LARGE', '生命週期事件超過本機紀錄大小上限')
        try:
            with FileLock(self.logdir / 'events.lock', timeout=30):
                current = self.logdir / 'events.jsonl'
                if current.exists() and current.stat().st_size + len(line) > self.max_bytes:
                    segments = sorted(self.logdir.glob('events.[0-9][0-9][0-9][0-9][0-9][0-9].jsonl'))
                    last = max((int(path.stem.split('.')[1]) for path in segments), default=0)
                    rotated = self.logdir / f'events.{last+1:06}.jsonl'
                    os.replace(current, rotated)
                    segments.append(rotated)
                    dropped = 0
                    while len(segments) > self.max_segments:
                        segments.pop(0).unlink()
                        dropped += 1
                    marker = b'{"event":"log_truncated","reason":"retention_limit"}\n'
                    if dropped and len(marker) + len(line) <= self.max_bytes:
                        with current.open('ab') as marker_file:
                            marker_file.write(marker)
                            marker_file.flush()
                            os.fsync(marker_file.fileno())
                    atomic_json(self.logdir / 'events_manifest.json',
                                {'current': current.name, 'segments': [path.name for path in segments],
                                 'truncated': bool(dropped)})
                with current.open('ab') as output:
                    output.write(line)
                    output.flush()
                    os.fsync(output.fileno())
        except (OSError, Timeout) as exc:
            raise FlowError('EVENT_PERSIST_FAILED', '既有生命週期事件無法落盤') from exc
        return safe

    def append(self, payload):
        self.logdir.mkdir(parents=True, exist_ok=True)
        line_size = len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 1
        if line_size > self.max_bytes:
            raise FlowError('EVENT_TOO_LARGE', '事件超過本機紀錄大小上限', name=payload.get('name'))
        try:
            with FileLock(self.logdir / 'flight.lock', timeout=30):
                self._rotate(line_size)
                self._write(payload, self.logdir / 'flight.jsonl')
                try:
                    manifest = self.logdir / 'flight_manifest.json'
                    if not manifest.exists():
                        atomic_json(manifest, {'schema_version': '1.0', 'current': 'flight.jsonl',
                                               'segments': [], 'truncated': False})
                    atomic_json(self.logdir / 'flight_checkpoint.json',
                                {'event_id': payload['event_id'], 'name': payload['name'],
                                 'timestamp_utc': payload['timestamp_utc']})
                except OSError:
                    pass
        except OSError as exc:
            raise FlowError('EVENT_PERSIST_FAILED', '關鍵事件無法在操作前落盤', name=payload.get('name')) from exc
        except Timeout as exc:
            raise FlowError('EVENT_LOCK_TIMEOUT', '事件寫入鎖逾時，操作尚未開始', name=payload.get('name')) from exc

    def read(self, *, cursor=None, limit=100):
        """Scan retained JSONL so a crash between fsync and SQLite index loses no event."""
        if not self.logdir.exists():
            return {'events': [], 'next_cursor': cursor, 'has_more': False, 'truncated': False}
        with FileLock(self.logdir / 'flight.lock', timeout=30):
            return self._read_unlocked(cursor=cursor, limit=limit)

    def _read_unlocked(self, *, cursor=None, limit=100):
        if limit < 1:
            raise ValueError('limit must be positive')
        retained = []
        for path in self._files():
            with path.open('rb') as source:
                for raw in source:
                    try:
                        event = json.loads(raw)
                    except (ValueError, UnicodeError):
                        continue
                    retained.append(event)
        start = 0
        missing_cursor = False
        if cursor is not None:
            match = next((index for index, item in enumerate(retained) if item.get('event_id') == cursor), None)
            if match is None:
                missing_cursor = True
            else:
                start = match + 1
        selected = retained[start:start+limit]
        manifest = self.logdir / 'flight_manifest.json'
        try:
            rotated_away = bool(json.loads(manifest.read_text(encoding='utf-8')).get('truncated')) if manifest.exists() else False
        except (OSError, ValueError):
            rotated_away = False
        return {'events': selected,
                'next_cursor': selected[-1].get('event_id') if selected else (None if missing_cursor else cursor),
                'has_more': start+limit < len(retained), 'truncated': rotated_away or missing_cursor}

    def rebuild_index(self):
        if not self.logdir.exists():
            return 0
        with FileLock(self.logdir / 'flight.lock', timeout=30):
            return self._rebuild_index_unlocked()

    def _rebuild_index_unlocked(self):
        count = 0
        if self.runtime is None:
            return count
        for path in self._files():
            with path.open('rb') as source:
                while True:
                    offset = source.tell()
                    raw = source.readline()
                    if not raw:
                        break
                    try:
                        payload = json.loads(raw)
                    except (ValueError, UnicodeError):
                        continue
                    if payload.get('event_id') and payload.get('name') and payload.get('timestamp_utc'):
                        self._index(payload, path.name, offset)
                        count += 1
        return count

"""Read only token metadata from a pinned Codex rollout or forwarded JSONL notifications.

Standard library only. Never connect to an unrelated App Server or silently pick
another window. See README for the distinction between observed receipts and billing.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens', 'total_tokens')
CAMEL = ('inputTokens', 'cachedInputTokens', 'cacheWriteInputTokens',
         'outputTokens', 'reasoningOutputTokens', 'totalTokens')
LIMIT = 16 * 1024 * 1024  # Skip oversized non-token transcript lines without retaining them.


def now():
    return datetime.now(timezone.utc).isoformat()


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize(value):
    if not isinstance(value, dict):
        return None
    result = {k: count(value.get(k, value.get(c))) for k, c in zip(FIELDS, CAMEL)}
    ins, cached, out = result['input_tokens'], result['cached_input_tokens'], result['output_tokens']
    if ins is None or out is None:
        return None
    if cached is not None and cached > ins:
        result['cached_input_tokens'] = cached = None
    reasoning = result['reasoning_output_tokens']
    if reasoning is not None and reasoning > out:
        result['reasoning_output_tokens'] = None
    result['uncached_input_tokens'] = ins - cached if cached is not None else None
    # Missing optional fields stay unknown, not zero. Never add cache/reasoning again.
    if result['total_tokens'] is None:
        result['total_tokens'] = ins + out
    return result


def select_file(file=None, session_id=None, latest=False, home=None):
    if file:
        return Path(file).expanduser().resolve(), 'explicit_file'
    root = Path(home or os.environ.get('CODEX_HOME', '~/.codex')).expanduser()
    if not session_id and not latest:
        return None, '未綁定 Session；設定 FLOW_BASE_CODEX_SESSION_FILE 或由宿主傳入 CODEX_THREAD_ID。'
    if session_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
        return None, 'Session ID 格式不合法。'
    pattern = f'rollout-*{session_id}.jsonl' if session_id else '*.jsonl'
    try:
        files = [p for name in ('sessions', 'archived_sessions')
                 for p in (root / name).rglob(pattern) if p.is_file()]
        if len(files) == 1 or (latest and files):
            return max(files, key=lambda p: p.stat().st_mtime_ns).resolve(), ('latest_unverified' if latest else 'thread_id')
        return None, '找不到對應 Session。' if not files else '找到多份對應 Session；請以 --file 明確指定。'
    except OSError:
        return None, '無法讀取 Session 目錄；請確認本機檔案權限。'


class SessionReader:
    """Incremental reader. Retains numeric metadata only; incomplete last lines wait."""
    def __init__(self, path, session_id=None):
        self.path = Path(path)
        self.expected = session_id
        self.offset = 0
        self.file_identity = None
        self.session_id = None
        self.model = None
        self.context = None
        self.latest = None
        self.priority = 0
        self.generation = 0
        self.invalid_lines = 0
        self.foreign = False

    def consume_notification(self, event):
        """Consume an actual message forwarded by the host's App Server connection."""
        if event.get('method') != 'thread/tokenUsage/updated':
            return
        p = event.get('params')
        if not isinstance(p, dict) or not isinstance(p.get('tokenUsage'), dict):
            return
        identity = p.get('threadId')
        if not isinstance(identity, str):
            return
        if self.expected and identity != self.expected:
            return
        if self.session_id and identity != self.session_id:
            # An unfiltered stream is ambiguous. Never let it hop between threads.
            self.foreign = True
            return
        self.session_id = identity
        value = p['tokenUsage']
        self.context = count(value.get('modelContextWindow'))
        self._accept(value.get('total'), 'app_server.thread/tokenUsage/updated', 3,
                     event, last=value.get('last'), receipt_id=p.get('turnId'))

    def _accept(self, values, source, priority, event, *, last=None, receipt_id=None):
        usage = normalize(values)
        if usage is None or priority < self.priority:
            return
        self.priority = priority
        self.latest = {'status': 'available', 'session_id': self.session_id, 'model': self.model,
                       'source': source, 'file': str(self.path), 'usage': usage,
                       'last': normalize(last), 'model_context_window': self.context,
                       'receipt_timestamp': event.get('timestamp'), 'receipt_id': receipt_id,
                       'record_offset': self.offset, 'generation': self.generation}

    def consume(self, event):
        if not isinstance(event, dict):
            return
        if event.get('method'):
            self.consume_notification(event)
            return
        p = event.get('payload')
        if not isinstance(p, dict):
            return
        kind = event.get('type')
        if kind == 'session_meta':
            identity = p.get('id')
            if isinstance(identity, str):
                if self.expected and identity != self.expected:
                    self.foreign = True
                if self.session_id and identity != self.session_id:
                    self.foreign = True
                self.session_id = identity
        elif kind == 'turn_context':
            if isinstance(p.get('model'), str):
                self.model = p['model']
        elif kind == 'token_usage_record':
            identity = p.get('thread_id') or self.session_id
            if self.expected and identity != self.expected:
                return
            if self.session_id and identity != self.session_id:
                self.foreign = True
                return
            self.session_id = identity
            self._accept(p.get('thread_token_usage'), 'token_usage_record.thread_token_usage', 2,
                         event, last=p.get('usage'), receipt_id=p.get('response_id'))
        elif kind == 'event_msg' and p.get('type') == 'token_count':
            info = p.get('info')
            if isinstance(info, dict):
                self.context = count(info.get('model_context_window'))
                self._accept(info.get('total_token_usage'), 'token_count.info.total_token_usage', 1,
                             event, last=info.get('last_token_usage'))

    def read(self):
        try:
            with self.path.open('rb') as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.file_identity is not None and (identity != self.file_identity or stat.st_size < self.offset):
                    self.offset = 0
                    self.latest = None
                    self.priority = 0
                    self.session_id = self.model = self.context = None
                    self.foreign = False
                    self.generation += 1
                self.file_identity = identity
                stream.seek(self.offset)
                while True:
                    begin = stream.tell()
                    raw = stream.readline(LIMIT + 1)
                    if not raw:
                        break
                    if len(raw) > LIMIT:
                        while raw and not raw.endswith(b'\n'):
                            raw = stream.readline(LIMIT + 1)
                        if not raw.endswith(b'\n'):
                            break
                        self.offset = stream.tell()
                        self.invalid_lines += 1
                        continue
                    if not raw.endswith(b'\n'):
                        # Writer may be halfway through a line. Do not consume it yet.
                        stream.seek(begin)
                        break
                    self.offset = stream.tell()
                    try:
                        event = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        self.invalid_lines += 1
                        continue
                    self.consume(event)
        except OSError as exc:
            return {'status': 'unavailable', 'reason': f'無法讀取已綁定檔案（{type(exc).__name__}）', 'file': str(self.path)}
        if self.foreign or (self.expected and self.session_id != self.expected):
            return {'status': 'unavailable', 'reason': 'Session 身分不符或來源包含多個 Thread；請明確綁定。'}
        if not self.latest or not self.session_id:
            return {'status': 'unavailable', 'reason': '已綁定檔案尚無可識別 Session 的累計用量收據。', 'file': str(self.path)}
        result = deepcopy(self.latest)
        result.update(model_context_window=self.context, invalid_lines=self.invalid_lines, observed_at=now())
        return result


class SessionMonitor:
    """Pin a source once, capture a baseline, and freeze a terminal snapshot."""
    def __init__(self, file=None, session_id=None, home=None):
        file = file or os.environ.get('FLOW_BASE_CODEX_SESSION_FILE')
        session_id = session_id or os.environ.get('FLOW_BASE_CODEX_THREAD_ID') or os.environ.get('CODEX_THREAD_ID')
        self.path, self.selection = select_file(file, session_id, home=home)
        self.reader = SessionReader(self.path, session_id) if self.path else None
        self.started_at = now()
        self.baseline = self._read()
        self.final = None

    def _read(self):
        return self.reader.read() if self.reader else {'status': 'unavailable', 'reason': self.selection}

    def summary(self, finish=False):
        if self.final is not None:
            return deepcopy(self.final)
        current = self._read()
        result = {**current, 'scope': 'codex_thread', 'selection': self.selection,
                  'measurement_started_at': self.started_at, 'baseline': self.baseline,
                  'run_delta': None, 'delta_status': 'unavailable',
                  'note': '僅反映觀測期間收到的同一 Thread 收據；可能包含並行活動，不含尚未回報及結束後的回覆。不得與流程 API 用量相加。'}
        before = self.baseline
        if current.get('status') == before.get('status') == 'available':
            same = all(current.get(k) == before.get(k) for k in ('session_id', 'source', 'generation'))
            a, b = before['usage'], current['usage']
            keys = (*FIELDS, 'uncached_input_tokens')
            monotonic = all(b[k] >= a[k] for k in keys if a[k] is not None and b[k] is not None)
            if same and monotonic:
                result['run_delta'] = {k: b[k] - a[k] if a[k] is not None and b[k] is not None else None for k in keys}
                changed = any(v not in (None, 0) for v in result['run_delta'].values())
                result['delta_status'] = 'observed' if changed else 'no_new_usage'
            else:
                result['delta_status'] = 'source_changed_or_counter_reset'
        if finish:
            self.final = deepcopy(result)
        return result


def add_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--file', type=Path, help='指定 rollout 或宿主轉送通知的 JSONL')
    group.add_argument('--latest', action='store_true', help='明確選擇最近修改檔案；不保證是目前視窗')
    parser.add_argument('--session-id', help='綁定／驗證 Thread ID')
    parser.add_argument('--codex-home', type=Path)


def query(args):
    identity = args.session_id or (None if args.latest else os.environ.get('FLOW_BASE_CODEX_THREAD_ID') or os.environ.get('CODEX_THREAD_ID'))
    file = args.file or (None if args.latest or args.session_id else os.environ.get('FLOW_BASE_CODEX_SESSION_FILE'))
    path, selection = select_file(file, identity, args.latest, args.codex_home)
    result = SessionReader(path, identity).read() if path else {'status': 'unavailable', 'reason': selection}
    result['selection'] = selection
    if args.latest:
        result['warning'] = '最近修改的 Session 不一定是目前 Codex 視窗；不可據此認定本次工作用量。'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    result = query(parser.parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'available' else 2


if __name__ == '__main__':
    sys.exit(main())

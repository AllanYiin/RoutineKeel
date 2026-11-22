"""Human lifecycle banners and machine-readable progress, independent of workflows."""
from __future__ import annotations

import json
import math
import os
import sys
import time
from contextvars import ContextVar
from pathlib import Path

from filelock import FileLock

ACTIVE = ContextVar('flow_base_reporter', default=None)


def clean(value):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))[:500]


def bar(done, total):
    ratio = done / total
    filled = min(24, int(ratio * 24))
    return '[' + '█' * filled + '░' * (24 - filled) + f'] {ratio * 100:5.1f}%'


def amount(done, total, unit):
    if unit == 'bytes':
        for divisor, label in ((1024**3,'GiB'),(1024**2,'MiB'),(1024,'KiB')):
            if total >= divisor:return f'{done/divisor:.2f}/{total/divisor:.2f} {label}'
    return f'{done:g}/{total:g} {unit}'


def append_console(path, rendered, *, max_bytes=4_000_000, max_segments=4):
    """Bound per-Run human logs without changing the current console.log path."""
    data = (rendered + '\n').encode('utf-8')
    if len(data) > max_bytes:
        data = data[:max_bytes-64] + b'\n[console entry truncated]\n'
    with FileLock(path.with_name('console.lock'), timeout=30):
        if path.exists() and path.stat().st_size + len(data) > max_bytes:
            segments = sorted(path.parent.glob('console.[0-9][0-9][0-9][0-9][0-9][0-9].log'))
            last = max((int(item.stem.split('.')[1]) for item in segments), default=0)
            rotated = path.parent / f'console.{last+1:06}.log'
            os.replace(path, rotated)
            segments.append(rotated)
            while len(segments) > max_segments:
                segments.pop(0).unlink()
                with path.open('ab') as marker:
                    marker.write(b'[earlier console log truncated]\n')
        with path.open('ab') as output:
            output.write(data)
            output.flush()


class Reporter:
    def __init__(self, title, flow_id, run_id, logdir, total_steps, runtime=None):
        self.title, self.flow_id, self.run_id = title, flow_id, run_id
        self.logdir, self.total_steps = Path(logdir), total_steps
        self.started = self.stage_started = time.monotonic()
        self.last_progress = 0.0
        self.index, self.label, self.step_id = 0, '環境準備與後端驗證', None
        self.measure = None
        self.finished = False
        from .codex_tokens import SessionMonitor
        from .usage import UsageMeter
        self.usage = UsageMeter(host=SessionMonitor())
        self.completed_steps = 0
        from .observability.events import EventRecorder
        self.event_recorder = EventRecorder(runtime, run_id, self.logdir)

    def emit(self, event, lines=(), *, banner=False, **data):
        from .runtime import atomic_json
        payload = {'event': event, 'flow_id': self.flow_id, 'run_id': self.run_id,
                   'workflow_title': self.title, 'step': self.step_id, 'step_index': self.index,
                   'total_steps': self.total_steps, 'label': self.label,
                   'elapsed_seconds': round(time.monotonic()-self.started, 1), **data}
        self.event_recorder.append_legacy(payload)
        from .diagnostics import redact
        atomic_json(self.logdir/'live_progress.json', redact(payload))
        # Keep an uncoloured human copy even when the caller requests JSON/quiet.
        # Preserve adapter-provided multi-line result links instead of flattening them.
        rendered_lines = [clean(part) for line in lines for part in str(line).splitlines()]
        rendered = '\n'.join(rendered_lines)
        if rendered:
            if banner:
                rule = '=' * 72
                rendered = '\n' + rule + '\n' + rendered + '\n' + rule + '\n'
            append_console(self.logdir / 'console.log', rendered)
            if banner or event == 'progress':
                heading = clean(rendered_lines[0]) if rendered_lines else event
                text = '# ' + heading + '\n\n' + '\n\n'.join(rendered_lines[1:]) + '\n'
                target = self.logdir / 'live_status.md'
                temporary = target.with_suffix('.md.tmp')
                temporary.write_text(text, encoding='utf-8')
                temporary.replace(target)
        mode = os.environ.get('FLOW_BASE_OUTPUT', 'human')
        if mode == 'quiet':
            return
        if mode == 'json':
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
            return
        if not rendered:
            return
        encoding = getattr(sys.stderr, 'encoding', None) or 'utf-8'
        rendered = rendered.encode(encoding, errors='replace').decode(encoding)
        if banner and getattr(sys.stderr, 'isatty', lambda: False)() and 'NO_COLOR' not in os.environ:
            colour = '31' if event in {'workflow_failed', 'workflow_interrupted'} else '36'
            rendered = f'\x1b[1;{colour}m{rendered}\x1b[0m'
        print(rendered, file=sys.stderr, flush=True)

    def references(self):
        return {'console_log': str(self.logdir / 'console.log'),
                'live_status': str(self.logdir / 'live_status.md'),
                'events': str(self.logdir / 'events.jsonl'),
                'flight_events': str(self.logdir / 'flight.jsonl'),
                'flight_manifest': str(self.logdir / 'flight_manifest.json'),
                'display_notice': '請將開始、節點與終止狀態轉述在對話正文；終端工具輸出可能被折疊。'}

    def start(self):
        self.emit('workflow_started', ['F L O W _ B A S E   >>>   WORKFLOW START', f'【FLOW_BASE｜工作流正式啟動】 {self.title}',
            f'流程識別：{self.flow_id}', f'本次執行：{self.run_id}',
            f'共 {self.total_steps} 個節點｜由已封裝流程執行', f'即時狀態：{self.logdir / "live_status.md"}'], banner=True)

    def environment_ready(self, prepared):
        device = prepared.get('device', '未指定')
        model = prepared.get('model')
        self.emit('environment_verified', ['【執行環境已驗證】',
                  f'實際裝置：{device}' + (f'｜模型：{model}' if model else ''),
                  f'運算模式：{prepared.get("compute_type", "由流程定義")}'],
                  device=device, compute_type=prepared.get('compute_type'), model=model,
                  execution_selection=prepared.get('execution_selection'))

    def enter(self, step, index, label):
        self.step_id, self.index, self.label = step.id, index, label
        self.stage_started, self.last_progress, self.measure = time.monotonic(), 0.0, None
        self.emit('step_started', ['F L O W _ B A S E   >>>   STEP START', f'【▶ 進入節點 {index}/{self.total_steps}】 {label}',
                  f'節點：{step.id}｜處理器：{step.handler}'], banner=True, handler=step.handler)

    def complete(self, status):
        elapsed = round(time.monotonic()-self.stage_started, 1)
        phrase = '快取命中，沿用已驗證結果' if status == 'cached' else '處理完成'
        self.completed_steps += 1
        self.emit('step_completed', ['F L O W _ B A S E   >>>   STEP DONE', f'【✓ 節點完成 {self.index}/{self.total_steps}】 {self.label}',
            f'{phrase}｜本節點耗時 {elapsed} 秒',
            f'節點完成度 {bar(self.index, self.total_steps)}（依節點數，非時間估算）'],
            banner=True, status=status, step_elapsed_seconds=elapsed)

    def progress(self, data):
        if self.finished: return
        now = time.monotonic()
        heartbeat = data.get('event') == 'heartbeat'
        if not heartbeat:
            if data.get('event') != 'progress': return
            done, total = data.get('completed'), data.get('total')
            if (isinstance(done, bool) or isinstance(total, bool) or
                not isinstance(done,(int,float)) or not isinstance(total,(int,float)) or
                not math.isfinite(done) or not math.isfinite(total) or not 0 <= done <= total or total <= 0): return
            unit, label = clean(data.get('unit', '單位')), clean(data.get('label', self.label))
            key = (label, unit, total)
            if self.measure and self.measure['key'] == key and done < self.measure['completed']: return
            self.measure = {'key': key, 'completed': done, 'total': total, 'unit': unit, 'label': label}
            if now-self.last_progress < 1 and done != total: return
        elif now-self.last_progress < 10:
            return
        elapsed = round(now-self.stage_started, 1)
        if self.measure:
            m = self.measure
            line = f'{m["label"]} {bar(m["completed"],m["total"])} {amount(m["completed"],m["total"],m["unit"])}｜本階段 {elapsed} 秒'
            progress = {k:v for k,v in m.items() if k != 'key'}
        else:
            line = f'【進行中 ⋯】 {self.label}｜已耗時 {elapsed} 秒｜此階段尚無可計算百分比'
            progress = {'indeterminate':True}
        self.last_progress = now
        self.emit('progress', [line], progress=progress, step_elapsed_seconds=elapsed)

    def token_receipt(self, request_id, usage=None, pending=False):
        if pending:self.usage.reserve(request_id)
        else:self.usage.settle(request_id,usage)
        self.emit('token_usage_updated', token_usage=self.usage.summary())

    def end(self, report, summary=None):
        self.finished = True
        summary = summary if isinstance(summary, dict) else {}
        label = summary.get('label', '結果狀態：'+str(report.get('status', '未提供')))
        from .usage import usage_lines
        tokens = self.usage.finish()
        self.emit('workflow_finished', ['F L O W _ B A S E   >>>   WORKFLOW END', f'【FLOW_BASE｜工作流處理結束】 {self.title}', label,
            f'已完成 {self.completed_steps}/{self.total_steps} 個節點｜總耗時 {time.monotonic()-self.started:.1f} 秒',
            f'輸出位置：{report.get("output", "")}', f'本次執行：{self.run_id}', *usage_lines(tokens)],
            banner=True, status=report.get('status'), result_label=label, output=report.get('output'), token_usage=tokens)

    def fail(self, code, message, interrupted=False, diagnostic=None):
        self.finished = True
        label = '工作流已中斷' if interrupted else '工作流執行失敗'
        from .observability.diagnostics import safe_export
        from .observability.errors import explain_error
        explanation = explain_error(code, cause_status=(diagnostic or {}).get('cause_status', 'unknown'))
        confidence = {'confirmed': '已確認', 'suspected': '可能', 'unknown': '尚未確認'}[explanation['cause_status']]
        error_id = (diagnostic or {}).get('error_id', '未取得')
        event_details = safe_export(diagnostic) if diagnostic else None
        if event_details is not None:
            event_details['message'] = explanation['user_message']
        from .usage import usage_lines
        tokens = self.usage.finish()
        self.emit('workflow_interrupted' if interrupted else 'workflow_failed',
            ['F L O W _ B A S E   >>>   WORKFLOW STOP', f'【FLOW_BASE｜{label}】 {self.title}', f'停在：{self.label}',
             f'原因：{explanation["user_title"]}｜{explanation["user_message"]}',
             f'確定程度：{confidence}｜錯誤碼：{code}｜錯誤編號：{error_id}',
             '下一步：' + explanation['suggested_actions'][0],
             f'紀錄：{self.logdir}', '未宣告工作流完成。', *usage_lines(tokens)],
            banner=True, error=code, error_message=explanation['user_message'],
            error_details=event_details,
            status='interrupted' if interrupted else 'failed',token_usage=tokens)

    def resource_event(self, name, data):
        if name in {'download_start','download_progress','download_verified'}:
            if name == 'download_start':
                self.measure = None
                self.resource_total = data.get('total_bytes')
            total = data.get('total_bytes', getattr(self,'resource_total',None))
            self.progress({'event':'progress','label':'下載 '+str(data.get('file','資源')),
                           'completed':data.get('received_bytes',0),'total':total,'unit':'bytes'})
            if name == 'download_verified':
                self.emit('resource_verified',[f'【✓ 資源校驗完成】 {data.get("file", "")}'])


def render_event(name, data):
    reporter = ACTIVE.get()
    if reporter and name.startswith('download_'):
        reporter.resource_event(name, data)
        return
    if not reporter and name.startswith('download_') and os.environ.get('FLOW_BASE_OUTPUT', 'human') == 'human':
        total, done = data.get('total_bytes'), data.get('received_bytes',0)
        if isinstance(total,(int,float)) and total > 0 and 0 <= done <= total:
            print(f'【資源下載】 {clean(data.get("file",""))} {bar(done,total)} {amount(done,total,"bytes")}',file=sys.stderr,flush=True)
        else:
            print(f'【資源校驗完成】 {clean(data.get("file",""))}',file=sys.stderr,flush=True)
        return
    labels = {'prepare_environment':'準備執行環境','check_dependencies':'檢查相依套件',
              'prepare_model':'準備模型資源','verify_backend':'驗證實際執行後端','execution_selected':'流程已選擇執行方式'}
    label = labels.get(name)
    if reporter and label:
        reporter.label, reporter.stage_started, reporter.measure = label, time.monotonic(), None
        reporter.emit(name, [f'【{label}】 ' + '｜'.join(f'{k}={clean(v)}' for k,v in data.items())], **data)
    elif os.environ.get('FLOW_BASE_OUTPUT','human') != 'quiet':
        if os.environ.get('FLOW_BASE_OUTPUT') == 'json' or not label:
            print(json.dumps({'event':name,**data}, ensure_ascii=False),file=sys.stderr,flush=True)
        else:
            print(f'【{label}】 '+ '｜'.join(f'{k}={clean(v)}' for k,v in data.items()),file=sys.stderr,flush=True)

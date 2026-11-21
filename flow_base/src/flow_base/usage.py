"""Provider-reported token receipts. Local result caches are not prompt caches."""
from __future__ import annotations

FIELDS=('input_total','input_cached','input_uncached','output')


def number(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None


def normalize_usage(usage):
    usage=usage if isinstance(usage,dict) else {}
    ins=number(usage.get('input_tokens',usage.get('prompt_tokens')))
    outs=number(usage.get('output_tokens',usage.get('completion_tokens')))
    detail=usage.get('input_tokens_details',usage.get('prompt_tokens_details'))
    cached=number(detail.get('cached_tokens')) if isinstance(detail,dict) else None
    if 'cache_read_input_tokens' in usage or 'cache_creation_input_tokens' in usage:
        # This receipt shape reports base input separately from cached read/write.
        cached=number(usage.get('cache_read_input_tokens'))
        created=number(usage.get('cache_creation_input_tokens'))
        ins=ins+cached+created if all(x is not None for x in (ins,cached,created)) else None
    if ins is not None and cached is not None and cached>ins:cached=None
    return {'input_total':ins,'input_cached':cached,
            'input_uncached':ins-cached if ins is not None and cached is not None else None,'output':outs}


class UsageMeter:
    def __init__(self, host=None):
        self.receipts = {}
        self.host = host

    def finish(self):
        if self.host:
            self.host.summary(finish=True)
        return self.summary()

    def reserve(self, request_id):
        self.receipts.setdefault(request_id,{k:None for k in FIELDS})

    def settle(self, request_id, usage):
        self.receipts[request_id]=normalize_usage(usage)

    def summary(self):
        values=list(self.receipts.values())
        return {'scope':'registered_workflow_api','registered_requests':len(values),
                **{k:sum(r[k] for r in values) if all(r[k] is not None for r in values) else None for k in FIELDS},
                'known_subtotals':{k:sum(r[k] for r in values if r[k] is not None) for k in FIELDS},
                'missing_usage_requests':sum(any(r[k] is None for k in FIELDS) for r in values),
                'source':'provider_usage_receipts',
                'host_agent_usage':self.host.summary() if self.host else {'status':'unavailable','reason':'未綁定 Codex Session'},
                'excludes':['host_agent_conversation','routing_before_workflow_start','unregistered_external_calls']}


def usage_lines(summary):
    def fmt(value):
        return f'{value:,}' if isinstance(value, int) and not isinstance(value, bool) else '未提供／不完整'
    host = summary.get('host_agent_usage', {})
    lines = ['【Codex Session Token｜宿主對話】']
    if host.get('status') == 'available':
        usage = host['usage']
        lines += [f"Session：{host.get('session_id')}",
                  f"對話累計輸入：{fmt(usage.get('input_tokens'))}｜已快取：{fmt(usage.get('cached_input_tokens'))}｜未快取：{fmt(usage.get('uncached_input_tokens'))}",
                  f"對話累計輸出：{fmt(usage.get('output_tokens'))}｜總計：{fmt(usage.get('total_tokens'))}",
                  f"其中推理輸出：{fmt(usage.get('reasoning_output_tokens'))}（已包含在輸出，不另加）",
                  f"模型 Context Window：{fmt(host.get('model_context_window'))}（容量，非耗用）"]
        delta = host.get('run_delta')
        if delta is not None:
            lines += [f"本次觀測增量｜輸入：{fmt(delta.get('input_tokens'))}｜已快取：{fmt(delta.get('cached_input_tokens'))}｜未快取：{fmt(delta.get('uncached_input_tokens'))}",
                      f"本次觀測增量｜輸出：{fmt(delta.get('output_tokens'))}｜總計：{fmt(delta.get('total_tokens'))}"]
            if host.get('delta_status') == 'no_new_usage':
                lines.append('尚未收到新增用量；觀測增量 0 不代表本輪對話成本為 0。')
        else:
            lines.append('本次增量無法計算：缺少開始收據，或來源／計數已重設。')
        lines.append('統計截至已收到的收據；結束後的回覆尚未計入。')
    else:
        lines += ['Codex／宿主對話用量：無法取得（不列為 0）', str(host.get('reason', '未綁定 Session'))]
    lines += ['【本次流程 API Token 統計｜獨立列示，不與宿主相加】',
              f"輸入總量：{fmt(summary.get('input_total'))}｜已快取：{fmt(summary.get('input_cached'))}｜未快取：{fmt(summary.get('input_uncached'))}",
              f"輸出：{fmt(summary.get('output'))}｜已登錄請求：{summary.get('registered_requests', 0)}",
              '已快取輸入是供應商 prompt cache，與節點結果快取不同。']
    return lines

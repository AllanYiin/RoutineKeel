"""Local review policy: never imports or calls an LLM transport."""
from flow_base.runtime import FlowError


def review(runtime, job, segments, settings, glossary, *, offline=False, transport=None):
    if settings.enabled or getattr(settings, 'max_calls', 0) != 0 or transport is not None:
        raise FlowError('LOCAL_LLM_FORBIDDEN', '本字幕 runner 硬預算為 0 次 LLM API 呼叫')
    return [dict(s) for s in segments], {
        'enabled': False, 'status': 'REVIEW_REQUIRED', 'accepted': [], 'pending': [], 'rejected': [],
        'warnings': [], 'usage': {'calls': 0, 'tokens': 0, 'committed_usd': 0},
        'policy': 'Local QA only; explicit bounded packet for external assistance; human approval required.'}

"""Task-owned delivery areas; independent of any domain's artifact formats."""
from __future__ import annotations

import re
from pathlib import Path

from .runtime import FlowError, atomic_bytes, atomic_json, digest, read_json, sha256

LAYOUT = 'categorized-v1'


def reject_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise FlowError('OUTPUT_LINK', '輸出路徑不可使用符號連結或 junction', path=str(part))


def choose_output(runtime, task, source=None, requested=None):
    workspace = runtime.home.parent.resolve()
    if requested is not None:
        path = Path(requested).expanduser()
        output = path if path.is_absolute() else workspace / path
    elif source is not None:
        source = Path(source).resolve()
        try:
            identity = {'flow_relative': source.relative_to(task.folder.resolve()).as_posix()}
        except ValueError:
            try:
                identity = {'workspace_relative': source.relative_to(workspace).as_posix()}
            except ValueError:
                identity = {'external_absolute': str(source)}
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', source.stem).strip(' .')[:64] or 'input'
        # A prefix avoids Windows reserved device names such as CON and AUX.
        output = task.folder / 'outputs' / ('job-' + stem + '-' + digest(identity)[:12])
    else:
        return None
    reject_links(output)
    output = output.resolve()
    if output == workspace or output in workspace.parents:
        raise FlowError('OUTPUT_PROTECTED', '請使用流程 outputs 內的個別工作資料夾', path=str(output))
    for name in ('flow_base', '.venv', 'share_states', 'share_logs'):
        protected = (workspace / name).resolve()
        if output == protected or output.is_relative_to(protected):
            raise FlowError('OUTPUT_PROTECTED', '不得將交付成果寫入底座、環境、狀態或日誌區', path=str(output))
    for folder in workspace.iterdir():
        if folder.is_dir() and (folder / 'flow.json').is_file():
            root = folder.resolve()
            if output == root or output.is_relative_to(root):
                allowed = root / 'outputs'
                if root != task.folder.resolve() or output == allowed or not output.is_relative_to(allowed):
                    raise FlowError('OUTPUT_PROTECTED', '流程內的成果只能放在目前流程 outputs 的個別工作資料夾', path=str(output))
    if source is not None:
        source = Path(source).resolve()
        if output == source or output in source.parents:
            raise FlowError('OUTPUT_CONFLICT', '輸入與輸出必須分開，不能覆寫輸入所在資料夾')
    return output


def output_layout(output, preferred='flat-v1'):
    """An existing owned flat result stays flat, including during review."""
    output = Path(output)
    reject_links(output)
    report = output / 'report.json'
    reject_links(report)
    if report.is_file():
        preferred = read_json(report).get('output_layout', 'flat-v1')
    if preferred not in ('flat-v1', LAYOUT):
        raise FlowError('OUTPUT_LAYOUT', '不支援的產物目錄版本', layout=preferred)
    return preferred


def artifact_directory(output, layout=None):
    output = Path(output)
    layout = output_layout(output) if layout is None else layout
    destination = output / 'artifacts' if layout == LAYOUT else output
    reject_links(destination)
    return destination


def validate_tree(output):
    output = Path(output)
    reject_links(output)
    if output.exists():
        for path in output.rglob('*'):
            reject_links(path)


def write_delivery_index(output, report):
    """Keep root metadata and relative delivery links portable, without transcript text."""
    output = Path(output)
    if report.get('output_layout') != LAYOUT:
        return
    validate_tree(output)
    status = str(report.get('status', 'unknown'))
    lines = ['# 本次工作成果', '', f'狀態：`{status}`', '',
             f'- flow_id：`{report.get("flow_id", "")}`',
             f'- job_id：`{report.get("job_id", "")}`',
             f'- run_id：`{report.get("run_id", "")}`', '',
             '實際成果位於 [artifacts/](../artifacts/)。領域驗收以 report.json 與流程的審閱資料為準。',
             'scripts/ 僅在任務確實交付腳本時存在；產出腳本不代表已執行。', '',
             '[機器可讀報告](../report.json) · [交付清單](../deliverables.json)', '']
    usage = report.get('token_usage')
    if usage:
        from .usage import usage_lines
        lines.extend(usage_lines(usage))
    atomic_bytes(output / 'docs' / 'run-summary.md', ('\n'.join(lines) + '\n').encode())
    files = []
    for category in ('docs', 'artifacts', 'scripts'):
        folder = output / category
        if folder.exists() and not folder.is_dir():
            raise FlowError('OUTPUT_LAYOUT', '分類路徑必須是資料夾', path=str(folder))
        if not folder.exists():
            continue
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                files.append({'path': path.relative_to(output).as_posix(), 'category': category,
                              'size_bytes': path.stat().st_size, 'sha256': sha256(path)})
    atomic_json(output / 'deliverables.json', {
        'schema_version': 1, 'output_layout': LAYOUT, 'flow_id': report.get('flow_id'),
        'job_id': report.get('job_id'), 'run_id': report.get('run_id'), 'status': status,
        'report': 'report.json', 'files': files})

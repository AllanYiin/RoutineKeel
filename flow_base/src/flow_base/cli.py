from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .engine import plan, run_workflow
from .intent import Intent, local_intent, model_intent, resolve
from .resources import export_resources, import_resources
from .runtime import FlowError, inspect_environment, read_json, atomic_json
from .workspace import Workspace, catalog
from .diagnostics import cli_error


def parser():
    p = argparse.ArgumentParser(prog='flow_base', description='單一工作區、多個獨立工作流程。無參數啟動自然語言互動入口。')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('--workspace', type=Path, help='工作區根目錄；啟動腳本會自動設定')
    commands = p.add_subparsers(dest='command')
    commands.add_parser('list', help='列出同層含 flow.json 的流程，不載入任務程式')
    from .codex_tokens import add_arguments
    add_arguments(commands.add_parser('tokens', help='讀取指定 Codex Session 的最新累計用量'))
    commands.add_parser('inspect', help='檢查作業系統、CPU、GPU、Python 與空間')
    c = commands.add_parser('action', help='呼叫流程自行宣告的能力；參數與驗證由流程包定義')
    c.add_argument('--task', required=True)
    c.add_argument('--name', required=True)
    c.add_argument('--params', type=Path, required=True, help='該能力的參數 JSON')
    commands.add_parser('catalog', help='提供宿主 Agent 的能力清單及 Intent Schema')
    c = commands.add_parser('history', help='查詢每次執行的 flow_id、run_id、job_id 與狀態')
    c.add_argument('--task')
    c.add_argument('--limit', type=int, default=30)
    c = commands.add_parser('chat', help='自然語言互動入口；每次先顯示可執行計畫')
    c.add_argument('--task', help='直接選擇流程名稱、資料夾或識別碼')
    c = commands.add_parser('launch', help='流程包的一鍵入口：準備所需資源並進入該流程')
    c.add_argument('--folder', type=Path, required=True)
    c.add_argument('--config', type=Path)
    c.add_argument('--offline', action='store_true')
    c.add_argument('--prepare-only', action='store_true', help='完成安裝檢查後結束，不進入互動')
    c.add_argument('--action', help='直接呼叫已宣告的流程能力，不先載入模型')
    c.add_argument('--params', type=Path, help='能力參數 JSON；未提供時使用空物件')
    for name in ('plan', 'prepare', 'verify', 'run', 'demo', 'cache-export', 'cache-import'):
        c = commands.add_parser(name)
        c.add_argument('--task', '--flow-id', dest='task', required=True, help='流程顯示名稱、資料夾名稱或 flow_id')
        c.add_argument('--config', type=Path, help='額外差異設定 JSON')
        c.add_argument('--input', type=Path, required=name in ('run', 'cache-import'))
        c.add_argument('--output', type=Path, required=name in ('demo', 'cache-export'),
                       help='預設為目前流程 outputs 內的工作資料夾；明確指定可另存')
        c.add_argument('--offline', action='store_true')
        if name == 'cache-export':
            c.add_argument('--resource', action='append', required=True)
    c = commands.add_parser('ask', help='將自然語言轉成計畫；--execute 執行')
    c.add_argument('text')
    c.add_argument('--input', type=Path)
    c.add_argument('--output', type=Path)
    c.add_argument('--router', choices=['local', 'llm'], default='local')
    c.add_argument('--execute', action='store_true')
    c.add_argument('--offline', action='store_true')
    c = commands.add_parser('dispatch', help='接收宿主 Agent 產生的 Intent JSON')
    c.add_argument('--intent', type=Path, required=True)
    c.add_argument('--execute', action='store_true')
    c.add_argument('--offline', action='store_true')
    return p


def route(workspace, intent, execute=False, offline=False, *, base_overrides=None):
    task, workflow, source, output, questions = resolve(workspace, intent, base_overrides=base_overrides)
    if questions:
        return {'status': 'needs_input', 'intent': intent.model_dump(), 'questions': questions, 'executed': False}
    if execute:
        return run_workflow(workspace.runtime, task, workflow, source, output, offline=offline)
    return {'status': 'planned', 'intent': intent.model_dump(), 'output': str(output),
            'plan': plan(workspace.runtime, task, workflow, source, output), 'executed': False}


def chat(workspace, selected=None, *, overrides=None, offline=False):
    print('flow_base：請描述需求。/list 列流程、/input 路徑 補輸入、/output 路徑 改輸出、/router llm 啟用已設定模型、/quit 離開。預設為離線常用語句模式。')
    available = catalog(workspace)
    if selected:
        print('目前流程：' + selected.manifest.title)
        print('請輸入 /input 檔案路徑，或描述需求；檢查計畫後輸入 /run 開始。')
        for example in selected.manifest.examples[:2]:
            print('例如：' + example)
    elif available:
        print('可用流程：' + '、'.join(f"{t['title']}（{t['folder']}）" for t in available))
    else:
        print('尚未放入流程包。請把含 flow.json 的流程資料夾放在 flow_base 同一層，再重新開啟。')
    pending = None
    draft = Intent(task=selected.manifest.id) if selected else None
    router = 'local'
    while True:
        try:
            line = input('> ').strip()
        except EOFError:
            break
        if line in {'/quit', '/exit'}:
            break
        try:
            if line == '/list':
                emit(catalog(workspace))
            elif line in {'/router llm', '/router local'}:
                if line == '/router llm' and selected and selected.manifest.llm_policy == 'forbidden':
                    raise FlowError('LOCAL_LLM_FORBIDDEN', '這個流程禁止 LLM API 呼叫；請用本機語句模式。')
                if offline and line == '/router llm':
                    raise FlowError('OFFLINE_LLM', '離線模式不能啟用 LLM 路由')
                router = line.split()[1]
                pending = None
                print('路由模式：' + router)
            elif line.startswith(('/input ', '/output ')):
                if draft is None:
                    print('請先描述要執行的流程。')
                    continue
                field, value = line.split(' ', 1)
                setattr(draft, field[1:], value.strip().strip('"「」'))
                result = route(workspace, draft, offline=offline, base_overrides=overrides)
                emit(result)
                pending = draft if result['status'] == 'planned' and result['plan']['ready_to_prepare'] else None
                if pending:
                    print('輸入 /run 執行這個計畫。')
            elif line == '/run':
                if pending is None:
                    print('請先輸入需求並取得完整計畫。')
                else:
                    emit(route(workspace, pending, execute=True, offline=offline, base_overrides=overrides))
                    pending = None
            elif line:
                intent = model_intent(workspace, line, selected=selected)[0] if router == 'llm' else local_intent(workspace, line, selected=selected)
                draft = intent
                result = route(workspace, intent, offline=offline, base_overrides=overrides)
                emit(result)
                pending = intent if result['status'] == 'planned' and result['plan']['ready_to_prepare'] else None
                if pending:
                    print('輸入 /run 執行這個計畫，或輸入新的需求。')
        except (FlowError, ValueError, OSError, KeyError) as exc:
            pending = None
            emit(cli_error(workspace.runtime, exc))
    return {'status': 'closed'}


def launch(workspace, args):
    import uuid
    task = workspace.task_at(args.folder)
    runtime = workspace.runtime
    if args.action:
        parameters = read_json(args.params) if args.params else {}
        if not isinstance(parameters, dict):
            raise FlowError('TASK_ACTION_PARAMETERS', '能力參數必須是 JSON object')
        print('正在執行流程能力：' + args.action, file=sys.stderr, flush=True)
        return task.invoke_action(runtime, args.action, parameters)
    launch_id = 'launch_' + uuid.uuid4().hex
    state_path = runtime.home / 'launcher' / (launch_id + '.json')
    state = {'launch_id': launch_id, 'flow_id': task.flow_id, 'title': task.manifest.title, 'status': 'preparing'}
    atomic_json(state_path, state)
    try:
        config = read_json(args.config) if args.config else {}
        print('正在準備流程：' + task.manifest.title, flush=True)
        adapter = task.load(runtime)
        workflow = task.configuration(runtime, config)
        if hasattr(adapter, 'describe'):
            for line in adapter.describe(workflow):
                print(line, flush=True)
        prepared = adapter.prepare(runtime, workflow, args.offline)
        verified = adapter.verify(runtime, workflow)
        if prepared.get('ok') is False or verified.get('ok') is False:
            raise FlowError('TASK_PREPARE', '流程環境驗證未通過', verification=verified)
    except (Exception, KeyboardInterrupt) as exc:
        state.update(status='failed', error=cli_error(runtime, exc)['error'])
        atomic_json(state_path, state)
        atomic_json(runtime.logs / 'launcher' / (launch_id + '.json'), state)
        print('流程準備失敗；排除錯誤後重新開啟同一入口即可。紀錄：' + str(state_path), file=sys.stderr)
        raise
    state.update(status='ready')
    atomic_json(state_path, state)
    print('流程準備完成：' + task.manifest.title, flush=True)
    if args.prepare_only:
        return {'status': 'ready', 'flow_id': task.flow_id, 'prepared': prepared, 'verified': verified}
    return chat(workspace, task, overrides=config, offline=args.offline)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None):
    args = parser().parse_args(argv)
    workspace = Workspace(args.workspace)
    runtime = workspace.runtime
    try:
        command = args.command or ('chat' if sys.stdin.isatty() else 'list')
        if command == 'tokens':
            from .codex_tokens import query
            result = query(args)
        elif command == 'list':
            result = catalog(workspace)
        elif command == 'catalog':
            result = {'runtime_api': '0.2', 'tasks': catalog(workspace), 'intent_schema': Intent.model_json_schema(),
                      'execution': 'dispatch --intent intent.json --execute', 'policy': 'Select installed IDs and declared parameter_paths only; no generated code.'}
        elif command == 'inspect':
            result = inspect_environment(runtime.home)
        elif command == 'action':
            parameters = read_json(args.params)
            if not isinstance(parameters, dict):
                raise FlowError('TASK_ACTION_PARAMETERS', '能力參數必須是 JSON object')
            result = workspace.task(args.task).invoke_action(runtime, args.name, parameters)
        elif command == 'history':
            flow_id = workspace.task(args.task).flow_id if args.task else None
            result = runtime.history(flow_id, max(1, min(args.limit, 1000)))
        elif command == 'chat':
            result = chat(workspace, workspace.task(args.task) if args.task else None)
        elif command == 'launch':
            result = launch(workspace, args)
        elif command in {'ask', 'dispatch'}:
            usage = None
            if command == 'dispatch':
                intent = Intent.model_validate(read_json(args.intent))
            elif args.router == 'llm':
                if args.offline:
                    raise FlowError('OFFLINE_LLM', 'LLM routing is unavailable offline')
                intent, usage = model_intent(workspace, args.text, source=args.input, output=args.output)
            else:
                intent = local_intent(workspace, args.text, source=args.input, output=args.output)
            result = route(workspace, intent, args.execute, args.offline)
            if usage:
                result['routing_usage'] = usage
            if command == 'ask' and args.router == 'local':
                result['routing_mode'] = 'offline_phrase_rules'
                result['routing_note'] = '常用語句比對並非完整語意理解；複雜需求請用宿主 Agent dispatch 或 --router llm。執行前請檢查參數。'
        else:
            task = workspace.task(args.task)
            adapter = task.load(runtime)
            config = read_json(args.config) if args.config else {}
            workflow = task.configuration(runtime, config)
            def local(path):
                return (workspace.root / path).resolve() if path is not None else None
            source, output = local(args.input), local(args.output)
            if command == 'plan':
                result = plan(runtime, task, workflow, source, output)
            elif command == 'prepare':
                result = adapter.prepare(runtime, workflow, args.offline)
            elif command == 'verify':
                result = adapter.verify(runtime, workflow)
            elif command == 'run':
                result = run_workflow(runtime, task, workflow, source, output, offline=args.offline)
            elif command == 'demo':
                if not hasattr(adapter, 'demo'):
                    raise FlowError('TASK_CAPABILITY', 'This task does not provide a demo')
                from .engine import run_demo
                result = run_demo(runtime, task, output)
            else:
                if not hasattr(adapter, 'resources'):
                    raise FlowError('TASK_CAPABILITY', 'This task has no downloadable resources')
                resources = adapter.resources()
                result = export_resources(runtime, output, args.resource, resources) if command == 'cache-export' else import_resources(runtime, source, resources)
        emit({'ok': True, 'result': result})
        return 0
    except FlowError as exc:
        emit(cli_error(runtime, exc))
        return 2
    except (ValidationError, ValueError, OSError, KeyError, ImportError) as exc:
        emit(cli_error(runtime, exc, 'INPUT_OR_ENVIRONMENT'))
        return 2
    except KeyboardInterrupt as exc:
        emit(cli_error(runtime, exc, 'INTERRUPTED'))
        return 130
    except Exception as exc:
        emit(cli_error(runtime, exc, 'UNEXPECTED_ERROR'))
        return 1

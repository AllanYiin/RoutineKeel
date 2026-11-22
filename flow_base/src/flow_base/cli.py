from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .intent import Intent, local_intent, model_intent
from .runtime import FlowError, inspect_environment, read_json, atomic_json
from .workspace import Workspace, catalog
from .diagnostics import cli_error
from .observability.diagnostics import guard_secondary
from .core.service import WorkflowService
from .core.types import PlanRequest, StartRequest, SubmitRequest


def parser():
    p = argparse.ArgumentParser(prog='flow_base', description='單一工作區、多個獨立工作流程。無參數啟動自然語言互動入口。')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('--workspace', type=Path, help='工作區根目錄；啟動腳本會自動設定')
    commands = p.add_subparsers(dest='command')
    commands.add_parser('list', help='列出同層含 flow.json 的流程，不載入任務程式')
    commands.add_parser('ui', help='開啟本機圖形工作台；無圖形環境時仍可用 CLI')
    commands.add_parser('mcp-stdio', help='啟動可選的本機 MCP stdio 工具入口')
    c = commands.add_parser('studio', help='對話訪談與可信本機文字確認')
    c.add_argument('operation')
    c.add_argument('--payload', default='{}', help='操作參數 JSON')
    c = commands.add_parser('queue-worker', help='另外啟動本機任務佇列 Worker；不由 MCP 派生')
    c.add_argument('--once', action='store_true', help='處理一件工作後退出；沒有工作時立即退出')
    c.add_argument('--poll-interval', type=float, default=0.5)
    c.add_argument('--idle-timeout', type=float, default=0.0, help='閒置多少秒後退出；0 表示持續等待')
    c = commands.add_parser('submit', help='驗證後把工作寫入持久化佇列，立即回傳 execution_id')
    c.add_argument('--task', required=True)
    c.add_argument('--input', type=Path, required=True)
    c.add_argument('--output', type=Path)
    c.add_argument('--config', type=Path)
    c.add_argument('--offline', action='store_true')
    c.add_argument('--idempotency-key')
    for name in ('execution-status', 'execution-result', 'execution-cancel'):
        c = commands.add_parser(name, help='以 execution_id 查詢或取消佇列工作')
        c.add_argument('execution_id')
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
    c = commands.add_parser('install-workflow', help='匯入訪談藍圖、試做或首次安裝')
    selector = c.add_mutually_exclusive_group(required=True)
    selector.add_argument('--archive', type=Path)
    selector.add_argument('--draft-id')
    c.add_argument('--member')
    c.add_argument('--offset', type=int, default=0)
    c.add_argument('--resolution', type=Path)
    c.add_argument('--cases', type=Path)
    c.add_argument('--build-id')
    c.add_argument('--install-hash')
    c = commands.add_parser('blueprint-task', help='取得待辦、回傳結果或退回重做')
    c.add_argument('session_id')
    c.add_argument('operation', choices=['next','claim','complete','revise'], nargs='?', default='next')
    c.add_argument('--payload', type=Path)
    c = commands.add_parser('update-workflow', help='預覽或套用新版流程 ZIP，保留設定、成果及歷史')
    c.add_argument('--archive', type=Path, required=True)
    c.add_argument('--task', help='通常從附件識別；指定時須與附件的流程識別碼相符')
    c.add_argument('--execute', action='store_true')
    c.add_argument('--plan-hash', help='預覽回傳的 plan_hash，綁定附件與目前版本')
    c = commands.add_parser('rollback-workflow-update', help='透過更新備份回復流程套件')
    c.add_argument('update_id')
    commands.add_parser('recover-workflow-update', help='復原中斷的流程包更新；保留使用者資料')
    for name, help_text in (
        ('doctor', '唯讀檢查本機環境與狀態'),
        ('diagnose', '依 run_id 查執行、診斷與最後事件'),
        ('logs', '列出指定 Run 的本機紀錄'),
        ('explain-error', '依 error_id 或錯誤碼顯示白話說明'),
        ('export-diagnostics', '預覽或明確匯出已遮罩診斷')):
        c = commands.add_parser(name, help=help_text)
        c.add_argument('--format', choices=['human', 'json'], default='human')
        if name in {'diagnose', 'logs', 'export-diagnostics'}:
            c.add_argument('run_id')
        elif name == 'explain-error':
            c.add_argument('identifier')
        if name == 'logs':
            c.add_argument('--tail', type=int, default=0, help='明確顯示至多 100 行已遮罩 console 尾端')
        if name == 'export-diagnostics':
            c.add_argument('--output', type=Path, help='明確指定新的本機 JSON 檔案')
            c.add_argument('--confirm', action='store_true', help='已檢視預覽並確認匯出')
    return p


def route(workspace, intent, execute=False, offline=False, *, base_overrides=None):
    from .engine import merge
    from .outputs import choose_output
    service = WorkflowService(workspace, authorized_external_paths=[intent.input, intent.output])
    if not intent.task:
        questions = intent.questions or ['請指定工作流程。']
        return {'status': 'needs_input', 'intent': intent.model_dump(), 'questions': questions, 'executed': False}
    task = workspace.task(intent.task, read_only=not execute)
    task.validate_intent_overrides(intent.overrides)
    questions = list(intent.questions)
    if not intent.input:
        questions.append('請提供輸入檔案路徑，或使用 --input。')
    source = service._path(intent.input, allow_external=True) if intent.input else None
    output = choose_output(workspace.runtime, task, source, intent.output)
    if questions:
        return {'status': 'needs_input', 'intent': intent.model_dump(), 'questions': questions, 'executed': False}
    overrides = merge(base_overrides or {}, intent.overrides)
    if execute:
        return service.run_workflow_sync(StartRequest(task=task.manifest.id, source=source,
            output=output, overrides=overrides, offline=offline, allow_external_paths=True))
    planned = service.plan_workflow(PlanRequest(task=task.manifest.id, source=source,
        output=output, overrides=overrides, offline=offline, allow_external_paths=True))
    return {'status': 'planned', 'intent': intent.model_dump(), 'output': str(output),
            'plan': planned.plan, 'executed': False}


def chat(workspace, selected=None, *, overrides=None, offline=False):
    print('flow_base：請描述需求。/list 列流程、/input 路徑 補輸入、/output 路徑 改輸出、/router llm 啟用已設定模型、/quit 離開。預設為離線常用語句模式。')
    available = WorkflowService(workspace).list_workflows()
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
                emit(WorkflowService(workspace).list_workflows())
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
            emit_error(workspace.runtime, exc)
    return {'status': 'closed'}


def launch(workspace, args):
    import uuid
    task = workspace.task_at(args.folder)
    runtime = workspace.runtime
    service = WorkflowService(workspace)
    if args.action:
        parameters = read_json(args.params) if args.params else {}
        if not isinstance(parameters, dict):
            raise FlowError('TASK_ACTION_PARAMETERS', '能力參數必須是 JSON object')
        print('正在執行流程能力：' + args.action, file=sys.stderr, flush=True)
        return service.invoke_action(task.manifest.id, args.action, parameters)
    launch_id = 'launch_' + uuid.uuid4().hex
    state_path = runtime.home / 'launcher' / (launch_id + '.json')
    state = {'launch_id': launch_id, 'flow_id': task.flow_id, 'title': task.manifest.title, 'status': 'preparing'}
    atomic_json(state_path, state)
    try:
        config = read_json(args.config) if args.config else {}
        print('正在準備流程：' + task.manifest.title, flush=True)
        launched = service.launch_prepare(task.manifest.id, overrides=config, offline=args.offline)
        for line in launched['description']:
            print(line, flush=True)
        prepared, verified = launched['prepared'], launched['verified']
    except (Exception, KeyboardInterrupt) as exc:
        primary = cli_error(runtime, exc)
        state.update(status='failed', error=primary['error'])
        diagnostic = primary['error']['diagnostic']
        guard_secondary(exc, diagnostic, 'launcher_state', lambda: atomic_json(state_path, state), runtime=runtime)
        guard_secondary(exc, diagnostic, 'launcher_log',
                        lambda: atomic_json(runtime.logs / 'launcher' / (launch_id + '.json'), state), runtime=runtime)
        guard_secondary(exc, diagnostic, 'launcher_notice',
                        lambda: print('流程準備失敗；排除錯誤後重新開啟同一入口即可。紀錄：' + str(state_path), file=sys.stderr),
                        runtime=runtime)
        raise
    state.update(status='ready')
    atomic_json(state_path, state)
    print('流程準備完成：' + task.manifest.title, flush=True)
    if args.prepare_only:
        return {'status': 'ready', 'flow_id': task.flow_id, 'prepared': prepared, 'verified': verified}
    return chat(workspace, task, overrides=config, offline=args.offline)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def emit_error(runtime, exc, code=None, *, human=False):
    """A broken diagnostic or output channel cannot replace the original error."""
    try:
        result = cli_error(runtime, exc, code)
    except BaseException:
        original_code = exc.code if isinstance(exc, FlowError) else type(exc).__name__
        result = {'ok': False, 'error': {'code': code or original_code,
                  'message': '錯誤診斷暫時無法保存', 'details': {}, 'diagnostic': {'error_id': 'unavailable'}}}
    try:
        if human:
            error = result['error']
            explanation = error.get('user_explanation', {})
            print(explanation.get('user_title', '流程無法啟動') + '。')
            blockers = error.get('details', {}).get('blockers', [])
            if isinstance(blockers, list) and len(blockers) == 1 and isinstance(blockers[0], dict) and blockers[0].get('code') == 'DISK_SPACE':
                required = blockers[0].get('required')
                if isinstance(required, (int, float)) and required > 0:
                    print(f'準備流程前，工作區所在磁碟需有約 {required / 1_000_000_000:.1f} GB 可用空間。')
            else:
                print(explanation.get('user_message', '請查看執行紀錄。'))
            for action in explanation.get('suggested_actions', []):
                print('• ' + action)
            if error.get('code') != 'PREFLIGHT_BLOCKED':
                print('問題代碼：' + error.get('code', 'UNKNOWN'))
            print('錯誤編號：' + error.get('diagnostic', {}).get('error_id', 'unavailable'))
        else:
            emit(result)
    except BaseException:
        try:
            print(f"{result['error']['code']}: {result['error']['diagnostic']['error_id']}", file=sys.stderr)
        except BaseException:
            pass
    return result


def diagnostic_command(workspace, args):
    from .observability.commands import (doctor, diagnose, logs, explain_error_id,
                                          export_preview, human_text)
    runtime = workspace.runtime
    try:
        if args.command == 'doctor':
            result = doctor(workspace)
        elif args.command == 'diagnose':
            result = diagnose(runtime, args.run_id)
        elif args.command == 'logs':
            result = logs(runtime, args.run_id, tail=args.tail)
        elif args.command == 'explain-error':
            result = explain_error_id(runtime, args.identifier)
        else:
            result = export_preview(runtime, args.run_id,
                                    destination=args.output, confirm=args.confirm)
        if args.format == 'json':
            emit({'ok': True, 'result': result})
        else:
            print(human_text(args.command, result))
        return 1 if (args.command == 'doctor' and result.get('status') == 'needs_attention') or (
            args.command == 'diagnose' and result.get('execution_status') in {'failed','interrupted','cancelled'}) else 0
    except FlowError as exc:
        if args.format == 'json':
            emit_error(runtime, exc)
        else:
            try:
                error = cli_error(runtime, exc)
                explanation = error['error'].get('user_explanation') or {}
                error_id = error['error']['diagnostic']['error_id']
            except BaseException:
                from .observability.errors import explain_exception
                explanation = explain_exception(exc)
                error_id = 'unavailable'
            print('問題：' + explanation.get('user_title', '診斷查詢失敗'), file=sys.stderr)
            print(explanation.get('user_message', '請記下錯誤編號。'), file=sys.stderr)
            if explanation.get('suggested_actions'):
                print('下一步：' + explanation['suggested_actions'][0], file=sys.stderr)
            print('錯誤編號：' + error_id, file=sys.stderr)
        return 2
    except Exception as exc:
        if args.format == 'json':
            emit_error(runtime, exc, 'UNEXPECTED_ERROR')
        else:
            try:
                result = cli_error(runtime, exc, 'UNEXPECTED_ERROR')
                error_id = result['error']['diagnostic']['error_id']
            except BaseException:
                error_id = 'unavailable'
            print('診斷功能發生未預期錯誤；請記下錯誤編號交給維護者。', file=sys.stderr)
            print('錯誤編號：' + error_id, file=sys.stderr)
        return 3


def main(argv=None):
    args = parser().parse_args(argv)
    workspace = Workspace(args.workspace)
    runtime = workspace.runtime
    service = WorkflowService(workspace, authorized_external_paths=[
        getattr(args, 'input', None), getattr(args, 'output', None), getattr(args, 'archive', None)])
    try:
        command = args.command or ('chat' if sys.stdin.isatty() else 'list')
        if command == 'studio':
            from .studio import studio_request, studio_terminal
            params = json.loads(args.payload)
            if not isinstance(params,dict):
                raise ValueError('訪談請求須為 JSON 物件')
            if args.operation in {'confirm','stream'}:
                return studio_terminal(workspace.root, params, operation=args.operation)
            send = studio_request(workspace.root, args.operation, params)
            print(json.dumps({'ok':True, 'result':send},ensure_ascii=False))
            return 0
        if command == 'ui':
            try:
                from .ui.app import run_ui
                run_ui(workspace.root)
                return 0
            except Exception as exc:
                if isinstance(exc, (ImportError, OSError)) or type(exc).__name__ == 'TclError':
                    print('本機圖形介面無法啟動；可使用原有 CLI。' + str(exc), file=sys.stderr)
                    return 2
                raise
        if command == 'mcp-stdio':
            from .adapters.mcp_stdio import run_stdio
            try:
                run_stdio(workspace.root)
                return 0
            except FlowError as exc:
                print(f'{exc.code}: {exc}', file=sys.stderr)
                return 2
        if command == 'tokens':
            from .codex_tokens import query
            result = query(args)
        elif command == 'list':
            result = service.list_workflows()
        elif command == 'catalog':
            result = {'runtime_api': '0.2', 'tasks': service.list_workflows(), 'intent_schema': Intent.model_json_schema(),
                      'execution': 'dispatch --intent intent.json --execute', 'policy': 'Select installed IDs and declared parameter_paths only; no generated code.'}
        elif command in {'doctor', 'diagnose', 'logs', 'explain-error', 'export-diagnostics'}:
            return diagnostic_command(workspace, args)
        elif command == 'install-workflow':
            result = service.install_workflow(archive=args.archive, draft_id=args.draft_id,
                resolution=read_json(args.resolution) if args.resolution else None,
                cases=read_json(args.cases) if args.cases else None, build_id=args.build_id,
                install_hash=args.install_hash, member=args.member, offset=args.offset)
        elif command == 'blueprint-task':
            result = service.blueprint_task(args.session_id, args.operation,
                                           read_json(args.payload) if args.payload else None)
        elif command == 'update-workflow':
            if args.execute:
                if not args.plan_hash:
                    raise FlowError('INVALID_CONFIGURATION', '套用更新前須預覽並提供 --plan-hash')
                result = service.update_workflow(args.archive, plan_hash=args.plan_hash, task=args.task)
            else:
                result = service.preview_workflow_update(args.archive, task=args.task)
        elif command == 'rollback-workflow-update':
            result = service.rollback_workflow_update(args.update_id)
        elif command == 'recover-workflow-update':
            result = service.recover_workflow_update()
        elif command == 'inspect':
            result = inspect_environment(runtime.home)
        elif command == 'action':
            parameters = read_json(args.params)
            result = service.invoke_action(args.task, args.name, parameters)
        elif command == 'history':
            result = service.history(args.task, args.limit)
        elif command == 'queue-worker':
            from .execution.queue_worker import run_queue_worker
            result = run_queue_worker(workspace.root, once=args.once,
                                      poll_interval=args.poll_interval,
                                      idle_timeout=args.idle_timeout)
        elif command == 'submit':
            config = read_json(args.config) if args.config else {}
            result = service.submit_workflow(SubmitRequest(
                task=args.task, source=args.input, output=args.output,
                overrides=config, offline=args.offline,
                idempotency_key=args.idempotency_key)).model_dump()
        elif command == 'execution-status':
            result = service.get_workflow_status(args.execution_id)
        elif command == 'execution-result':
            result = service.get_workflow_result(args.execution_id)
        elif command == 'execution-cancel':
            result = service.cancel_workflow(args.execution_id)
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
        elif command == 'plan':
            config = read_json(args.config) if args.config else {}
            result = service.plan_workflow(PlanRequest(task=args.task, source=args.input,
                output=args.output, overrides=config, offline=args.offline,
                allow_external_paths=True)).plan
        elif command == 'run':
            config = read_json(args.config) if args.config else {}
            result = service.run_workflow_sync(StartRequest(task=args.task, source=args.input,
                output=args.output, overrides=config, offline=args.offline,
                allow_external_paths=True))
        elif command == 'prepare':
            config = read_json(args.config) if args.config else {}
            result = service.prepare_workflow(args.task, overrides=config, offline=args.offline)
        elif command == 'verify':
            config = read_json(args.config) if args.config else {}
            result = service.verify_workflow(args.task, overrides=config)
        elif command == 'demo':
            config = read_json(args.config) if args.config else {}
            result = service.demo_workflow(args.task, args.output, overrides=config)
        elif command == 'cache-export':
            config = read_json(args.config) if args.config else {}
            result = service.transfer_cache(args.task, direction='export', output=args.output,
                                            resources=args.resource, overrides=config)
        elif command == 'cache-import':
            config = read_json(args.config) if args.config else {}
            result = service.transfer_cache(args.task, direction='import', source=args.input,
                                            overrides=config)
        else:
            raise FlowError('INVALID_CONFIGURATION', '未知命令')
        emit({'ok': True, 'result': result})
        return 0
    except FlowError as exc:
        emit_error(runtime, exc, human=command == 'launch')
        return 2
    except (ValidationError, ValueError, OSError, KeyError, ImportError) as exc:
        emit_error(runtime, exc, 'INPUT_OR_ENVIRONMENT')
        return 2
    except KeyboardInterrupt as exc:
        emit_error(runtime, exc, 'INTERRUPTED')
        return 130
    except Exception as exc:
        emit_error(runtime, exc, 'UNEXPECTED_ERROR')
        return 1

"""Optional MCP stdio facade. WorkflowService owns every policy and state change."""
from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from .. import __version__
from ..core.service import WorkflowService
from ..core.types import PlanRequest, SubmitRequest
from ..observability.errors import explain_exception
from ..runtime import FlowError

LOG = logging.getLogger(__name__)


def create_server(workspace: Path | str):
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise FlowError('DEPENDENCY_UNAVAILABLE', 'MCP 選配套件未安裝；請安裝 flow_base[mcp]') from exc

    service = WorkflowService(workspace)
    server = MCPServer('RoutineKeel', version=__version__,
                       instructions='Only installed workflows and paths inside this local workspace are available.')

    def result(call):
        try:
            return {'ok': True, 'result': call()}
        except FlowError as exc:
            explanation = explain_exception(exc)
            return {'ok': False, 'error': {'code': exc.code,
                    'message': explanation['user_message'],
                    'error_id': getattr(exc, 'error_id', None)}}
        except (ValidationError, ValueError, TypeError) as exc:
            LOG.info('MCP input rejected: %s', type(exc).__name__)
            return {'ok': False, 'error': {'code': 'INVALID_CONFIGURATION',
                    'message': '請檢查工具參數格式與必填欄位。', 'error_id': None}}
        except Exception as exc:
            LOG.exception('MCP tool failed: %s', type(exc).__name__)
            return {'ok': False, 'error': {'code': 'UNEXPECTED_INTERNAL_ERROR',
                    'message': '本機工具發生未預期錯誤；請查看本機診斷紀錄。', 'error_id': None}}

    @server.tool()
    def list_workflows() -> dict:
        """List installed workflows in the selected local workspace."""
        return result(service.list_workflows)

    @server.tool()
    def describe_workflow(workflow_id: str) -> dict:
        """Describe one installed workflow and its declared capabilities."""
        return result(lambda: service.describe_workflow(workflow_id))

    @server.tool()
    def install_workflow(archive: str | None = None, draft_id: str | None = None,
                         resolution: dict | None = None, cases: list[dict] | None = None,
                         build_id: str | None = None, install_hash: str | None = None,
                         member: str | None = None, offset: int = 0) -> dict:
        """Import a workspace blueprint, trial a resolution, or install a verified build by hash."""
        return result(lambda: service.install_workflow(archive=archive, draft_id=draft_id,
            resolution=resolution, cases=cases, build_id=build_id, install_hash=install_hash,
            member=member, offset=offset))

    @server.tool()
    def blueprint_task(session_id: str, operation: str = 'next', payload: dict | None = None) -> dict:
        """Read the next pinned task, claim an external action, submit evidence, or revise a step."""
        return result(lambda: service.blueprint_task(session_id, operation, payload))

    @server.tool()
    def preview_workflow_update(archive: str, workflow_id: str | None = None) -> dict:
        """Inspect a user-provided workflow ZIP inside the workspace, without running its code."""
        return result(lambda: service.preview_workflow_update(archive, task=workflow_id))

    @server.tool()
    def update_workflow(archive: str, plan_hash: str, workflow_id: str | None = None) -> dict:
        """Apply the user's requested update with the preview plan_hash. Preserve local settings and outputs."""
        return result(lambda: service.update_workflow(archive, plan_hash=plan_hash, task=workflow_id))

    @server.tool()
    def rollback_workflow_update(update_id: str) -> dict:
        """Restore package files from one recorded update when the user requests rollback."""
        return result(lambda: service.rollback_workflow_update(update_id))

    @server.tool()
    def recover_workflow_update() -> dict:
        """Restore the old package after an interrupted update; never delete user data."""
        return result(service.recover_workflow_update)

    @server.tool()
    def plan_workflow(workflow_id: str, source: str | None = None, output: str | None = None,
                      overrides: dict | None = None, offline: bool = False) -> dict:
        """Preview a workflow without modifying workspace state."""
        return result(lambda: service.plan_workflow(PlanRequest(
            task=workflow_id, source=source, output=output,
            overrides=overrides or {}, offline=offline)).model_dump())

    @server.tool()
    def submit_workflow(workflow_id: str, source: str, output: str | None = None,
                        overrides: dict | None = None, offline: bool = False,
                        idempotency_key: str | None = None,
                        execution_spec_hash: str | None = None) -> dict:
        """Persist a queued request and return execution_id before any Run exists."""
        return result(lambda: service.submit_workflow(SubmitRequest(
            task=workflow_id, source=source, output=output, overrides=overrides or {},
            offline=offline, idempotency_key=idempotency_key,
            expected_spec_hash=execution_spec_hash)).model_dump())

    @server.tool()
    def get_workflow_status(execution_id: str) -> dict:
        """Read durable queue and Run state with an explicit execution_id."""
        return result(lambda: service.get_workflow_status(execution_id))

    @server.tool()
    def get_workflow_result(execution_id: str) -> dict:
        """Return verified artifact metadata and human review status after completion."""
        return result(lambda: service.get_workflow_result(execution_id))

    @server.tool()
    def cancel_workflow(execution_id: str) -> dict:
        """Cancel queued work or request cooperative stop for an active Run."""
        return result(lambda: service.cancel_workflow(execution_id))

    from .studio_tools import register
    @server.tool()
    def agent_workflow(execution_id:str|None=None,session_id:str|None=None,proposal_id:str|None=None)->dict:
        """Discover installed workflows or reconnect to a persisted execution, job or version proposal."""
        return result(lambda:service.agent_workflow(execution_id=execution_id,session_id=session_id,proposal_id=proposal_id))

    @server.tool()
    def workflow_evolution(session_id: str, operation: str, payload: dict) -> dict:
        """Propose a workflow revision from feedback, compare candidates or record a desktop decision."""
        return result(lambda: service.workflow_evolution(session_id, operation, payload))

    @server.tool()
    def workflow_quality(session_id:str,operation:str="preview",payload:dict | None=None) -> dict:
        """Preview a graph result and record version-bound feedback from this desktop conversation."""
        return result(lambda:service.workflow_quality(session_id,operation,payload))
    @server.tool()
    def resolve_blueprint_capabilities(draft_id: str, offline: bool=False) -> dict:
        return result(lambda: service.resolve_blueprint_capabilities(draft_id,offline=offline))
    register(server, service.workspace.root, result)
    return server


def run_stdio(workspace: Path | str) -> None:
    create_server(workspace).run(transport='stdio')

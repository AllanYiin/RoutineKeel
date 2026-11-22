"""Host tools submit proposals. Human confirmation is a separate terminal action."""

from ..studio import studio_request


def register(server, workspace, result):
    def call(operation, **params):
        return result(lambda: studio_request(workspace, operation, params))

    @server.tool()
    def studio_create_project(name: str, task: str, request_id: str | None = None) -> dict:
        return call("create", name=name, task=task, request_id=request_id)

    @server.tool()
    def studio_list_projects(include_archived: bool = False) -> dict:
        return call("list", include_archived=include_archived)

    @server.tool()
    def studio_get_project(project_id: str) -> dict:
        return call("get", project_id=project_id)

    @server.tool()
    def studio_update_project(
        project_id: str, expected_revision: int, name: str | None = None, goal: dict | None = None
    ) -> dict:
        return call(
            "update",
            project_id=project_id,
            expected_revision=expected_revision,
            name=name,
            goal=goal,
        )

    @server.tool()
    def studio_start_interview(project_id: str, expected_revision: int) -> dict:
        return call("start", project_id=project_id, expected_revision=expected_revision)

    @server.tool()
    def studio_archive_project(project_id: str, expected_revision: int) -> dict:
        return call("archive", project_id=project_id, expected_revision=expected_revision)

    @server.tool()
    def studio_restore_project(project_id: str, expected_revision: int) -> dict:
        return call("restore", project_id=project_id, expected_revision=expected_revision)

    @server.tool()
    def studio_next_question(project_id: str, domain: str | None = None) -> dict:
        return call("next", project_id=project_id, domain=domain)

    @server.tool()
    def studio_submit_answer(
        project_id: str,
        expected_revision: int,
        request_id: str,
        domain: str,
        value: object,
        evidence_refs: list[str],
        confidence: float,
        status: str = "inferred",
        impact: str = "business",
    ) -> dict:
        """Save a proposal or unresolved gap. User confirmation requires the local text CLI."""
        return call(
            "answer",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            domain=domain,
            value=value,
            evidence_refs=evidence_refs,
            confidence=confidence,
            status=status,
            impact=impact,
        )

    @server.tool()
    def studio_pause_interview(project_id: str, expected_revision: int, request_id: str) -> dict:
        return call(
            "pause",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
        )

    @server.tool()
    def studio_resume_interview(project_id: str, expected_revision: int, request_id: str) -> dict:
        return call(
            "resume",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
        )

    @server.tool()
    def studio_preview_source(project_id: str, source_id: str) -> dict:
        """Show a source already imported into this project."""
        return call("source", project_id=project_id, source_id=source_id)

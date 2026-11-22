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
    def studio_next_questions(project_id: str, domains: list[str] | None = None) -> dict:
        """Fetch related questions at one revision to present together in the conversation."""
        return call("next_batch", project_id=project_id, domains=domains)

    @server.tool()
    def studio_record_reply(
        project_id: str,
        expected_revision: int,
        request_id: str,
        domain: str,
        question_id: str,
        user_text: str,
        response_kind: str = "answer",
        message_ref: str | None = None,
    ) -> dict:
        """After the desktop user answers, relay their actual words for this displayed question."""
        return call(
            "reply",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            domain=domain,
            question_id=question_id,
            user_text=user_text,
            response_kind=response_kind,
            message_ref=message_ref,
        )

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
        """Save a host proposal or unresolved gap. Record the user's next turn via studio_record_reply."""
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
    def studio_record_replies(
        project_id: str,
        expected_revision: int,
        request_id: str,
        user_text: str,
        replies: list[dict],
        message_ref: str | None = None,
    ) -> dict:
        """Save one user's message across answered questions.

        Each reply has domain, question_id and optionally answer_text or response_kind.
        """
        return call(
            "reply_batch",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            user_text=user_text,
            replies=replies,
            message_ref=message_ref,
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
    def studio_preview_blueprint(
        project_id: str,
        expected_revision: int,
        request_id: str,
        graph: dict,
        design: dict,
        skills: list[dict] | None = None,
    ) -> dict:
        """Prepare the design summary and key review points for display in the conversation."""
        return call(
            "prepare_blueprint",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            graph=graph,
            design=design,
            skills=skills or [],
        )

    @server.tool()
    def studio_export_blueprint(
        project_id: str,
        expected_revision: int,
        request_id: str,
        proposal_id: str,
        decision: str,
        user_text: str,
    ) -> dict:
        """Record the desktop user's actual response to the displayed design proposal."""
        return call(
            "export_blueprint",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            proposal_id=proposal_id,
            decision=decision,
            user_text=user_text,
        )

    @server.tool()
    def studio_preview_source(project_id: str, source_id: str) -> dict:
        """Show a source already imported into this project."""
        return call("source", project_id=project_id, source_id=source_id)

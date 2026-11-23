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
    def studio_extract_pattern(
        project_id: str,
        workflow_ref: str,
        selected_nodes: list[str],
        purpose: str,
        pattern_id: str,
        expected_revision: int,
        request_id: str,
        capability_map: dict | None = None,
        portable_parameters: dict[str, list[str]] | None = None,
        version: str = "1.0.0",
    ) -> dict:
        """Draft an inert Pattern from the checked installed workflow version."""
        return call(
            "extract_pattern",
            project_id=project_id,
            workflow_ref=workflow_ref,
            selected_nodes=selected_nodes,
            purpose=purpose,
            pattern_id=pattern_id,
            expected_revision=expected_revision,
            request_id=request_id,
            version=version,
            capability_map=capability_map or {},
            portable_parameters=portable_parameters or {},
        )

    @server.tool()
    def studio_get_pattern_extraction(project_id: str, extraction_id: str) -> dict:
        return call("get_extraction", project_id=project_id, extraction_id=extraction_id)

    @server.tool()
    def studio_export_pattern_extraction(project_id: str, extraction_id: str) -> dict:
        """Write a sanitised draft ZIP inside the local workspace."""
        return call("export_extraction", project_id=project_id, extraction_id=extraction_id)

    @server.tool()
    def studio_qualify_pattern(pack_ref: str, pattern_ref: str) -> dict:
        """Replay a version's reviewed test suite in the isolated Builder."""
        return call("qualify_pattern", pack_ref=pack_ref, pattern_ref=pattern_ref)

    @server.tool()
    def studio_get_pattern_trial(pattern_ref: str, qualification_hash: str) -> dict:
        return call(
            "get_pattern_trial", pattern_ref=pattern_ref, qualification_hash=qualification_hash
        )

    @server.tool()
    def studio_recommend_patterns(
        project_id: str,
        expected_revision: int,
        request_id: str,
        context: dict,
        top_k: int = 5,
    ) -> dict:
        """Find applicable Pattern candidates for the current design revision."""
        return call(
            "recommend_patterns",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            context=context,
            top_k=top_k,
        )

    @server.tool()
    def studio_decide_pattern(
        project_id: str,
        expected_revision: int,
        request_id: str,
        recommendation_id: str,
        decision: str,
        user_text: str,
        message_ref: str | None = None,
    ) -> dict:
        """Relay the user's actual response to a displayed Pattern recommendation."""
        return call(
            "decide_pattern",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            recommendation_id=recommendation_id,
            decision=decision,
            user_text=user_text,
            message_ref=message_ref,
        )

    @server.tool()
    def studio_inspect_pattern(pattern_ref: str, level: str, project_id: str | None = None) -> dict:
        return call("inspect_pattern", pattern_ref=pattern_ref, level=level, project_id=project_id)

    @server.tool()
    def studio_configure_patterns(
        project_id: str,
        expected_revision: int,
        request_id: str,
        enabled: bool,
    ) -> dict:
        """Enable or disable design-stage recommendations for this interview."""
        return call(
            "configure_patterns",
            project_id=project_id,
            expected_revision=expected_revision,
            request_id=request_id,
            enabled=enabled,
        )

    @server.tool()
    def studio_compose_pattern(
        project_id: str,
        draft_id: str,
        expected_revision: int,
        request_id: str,
        pattern_ref: str,
        insert_location: dict,
        port_map: dict,
        parameter_bindings: dict,
        decision_id: str,
        golden_cases: list[dict] | None = None,
        improvement_id: str | None = None,
        target_trial: str | None = None,
    ) -> dict:
        """Expand an accepted Pattern in a new Blueprint draft revision."""
        return call(
            "compose_pattern",
            project_id=project_id,
            draft_id=draft_id,
            expected_revision=expected_revision,
            request_id=request_id,
            pattern_ref=pattern_ref,
            insert_location=insert_location,
            port_map=port_map,
            parameter_bindings=parameter_bindings,
            decision_id=decision_id,
            golden_cases=golden_cases,
            improvement_id=improvement_id,
            target_trial=target_trial,
        )

    @server.tool()
    def studio_generalize_patterns(
        pattern_refs: list[str],
        pattern_id: str,
        version: str,
        variant: int | None = None,
        title: str | None = None,
        profile: dict | None = None,
        profile_mappings: dict[str, str] | None = None,
    ) -> dict:
        """Compare source contracts and return an inert common draft or explicit variant."""
        return call(
            "generalize_patterns",
            pattern_refs=pattern_refs,
            pattern_id=pattern_id,
            version=version,
            variant=variant,
            title=title,
            profile=profile,
            profile_mappings=profile_mappings,
        )

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

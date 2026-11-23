"""Ordered, transactional Studio schema migrations."""

APPLICATION_ID = 0x53545544  # STUD; rejects accidentally selected Runtime databases.
SCHEMA_VERSION = 18

V001 = (
    """CREATE TABLE studio_projects (
        project_id TEXT PRIMARY KEY, name TEXT NOT NULL, goal_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('active','archived','deleted')),
        version INTEGER NOT NULL CHECK(version > 0), current_revision_id TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE studio_revisions (
        revision_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        parent_id TEXT REFERENCES studio_revisions(revision_id),
        ir_json TEXT NOT NULL, ir_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('draft','discovering','needs_clarification',
            'ir_ready','generating','verifying','verified','rejected','awaiting_approval',
            'approved','published')),
        discarded INTEGER NOT NULL DEFAULT 0 CHECK(discarded IN (0,1)),
        created_at TEXT NOT NULL)""",
    "CREATE INDEX studio_revision_project ON studio_revisions(project_id, created_at)",
    """CREATE TRIGGER immutable_revision_content BEFORE UPDATE OF
        revision_id, project_id, parent_id, ir_json, ir_hash, created_at ON studio_revisions
        BEGIN SELECT RAISE(ABORT, 'immutable revision'); END""",
    """CREATE TRIGGER immutable_published_revision BEFORE UPDATE ON studio_revisions
        WHEN OLD.status = 'published'
        BEGIN SELECT RAISE(ABORT, 'immutable published revision'); END""",
    """CREATE TRIGGER retain_revision_history BEFORE DELETE ON studio_revisions
        BEGIN SELECT RAISE(ABORT, 'retain revision history'); END""",
    """CREATE TRIGGER parent_project_scope BEFORE INSERT ON studio_revisions
        WHEN NEW.parent_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM studio_revisions WHERE revision_id=NEW.parent_id
            AND project_id=NEW.project_id)
        BEGIN SELECT RAISE(ABORT, 'parent belongs to another project'); END""",
)

V002 = (
    """CREATE TABLE studio_sources (
        source_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        current_version INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','removed')))""",
    """CREATE TABLE studio_source_versions (
        source_id TEXT NOT NULL REFERENCES studio_sources(source_id), version INTEGER NOT NULL,
        name TEXT NOT NULL, kind TEXT NOT NULL, content_hash TEXT NOT NULL,
        manifest_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(source_id,version))""",
    """CREATE TABLE studio_source_blobs (
        sha256 TEXT PRIMARY KEY, content BLOB NOT NULL)""",
    """CREATE TRIGGER immutable_source_version BEFORE UPDATE ON studio_source_versions
        BEGIN SELECT RAISE(ABORT, 'immutable source version'); END""",
    """CREATE TRIGGER retain_source_versions BEFORE DELETE ON studio_source_versions
        BEGIN SELECT RAISE(ABORT, 'retain source history'); END""",
    "CREATE INDEX studio_sources_project ON studio_sources(project_id)",
)

V003 = (
    """CREATE TABLE studio_tools (
        tool_id TEXT NOT NULL, version TEXT NOT NULL, contract_json TEXT NOT NULL,
        contract_hash TEXT NOT NULL, trust_status TEXT NOT NULL CHECK(trust_status IN ('builtin','untrusted','approved')),
        status TEXT NOT NULL CHECK(status IN ('active','disabled','archived')),
        record_version INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(tool_id,version))""",
    """CREATE TABLE studio_tool_approvals (
        id INTEGER PRIMARY KEY, tool_id TEXT NOT NULL, version TEXT NOT NULL,
        contract_hash TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(tool_id,version) REFERENCES studio_tools(tool_id,version))""",
    """CREATE TRIGGER immutable_tool_contract BEFORE UPDATE OF tool_id,version,contract_json,contract_hash ON studio_tools
        BEGIN SELECT RAISE(ABORT, 'immutable tool contract'); END""",
)

V004 = (
    """CREATE TABLE studio_events (
        sequence INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        revision_id TEXT, call_id TEXT, event TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX studio_events_project ON studio_events(project_id,sequence)",
    """CREATE TABLE studio_llm_calls (
        call_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        revision_id TEXT, run_id TEXT, step_id TEXT, phase TEXT NOT NULL, model TEXT NOT NULL,
        request_hash TEXT NOT NULL, reserved INTEGER NOT NULL, charged INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
        status TEXT NOT NULL, created_at TEXT NOT NULL)""",
)

V005 = (
    """CREATE TABLE studio_evidence_graphs (
        graph_id TEXT NOT NULL, version INTEGER NOT NULL,
        project_id TEXT NOT NULL REFERENCES studio_projects(project_id), payload_json TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(graph_id,version))""",
    """CREATE TABLE studio_test_cases (
        case_id TEXT NOT NULL, version INTEGER NOT NULL,
        project_id TEXT NOT NULL REFERENCES studio_projects(project_id), spec_json TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(case_id,version))""",
    """CREATE TRIGGER immutable_golden_case BEFORE UPDATE ON studio_test_cases
        BEGIN SELECT RAISE(ABORT, 'immutable golden case'); END""",
    """CREATE TRIGGER immutable_evidence_graph BEFORE UPDATE ON studio_evidence_graphs
        BEGIN SELECT RAISE(ABORT, 'immutable evidence graph'); END""",
)

V006 = (
    """CREATE TABLE studio_builds (
        build_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        revision_id TEXT NOT NULL REFERENCES studio_revisions(revision_id), relative_path TEXT NOT NULL,
        package_hash TEXT NOT NULL, status TEXT NOT NULL, mapping_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
)

V007 = (
    """CREATE TABLE studio_verifications (
        report_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        build_id TEXT NOT NULL REFERENCES studio_builds(build_id), report_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TRIGGER immutable_verification BEFORE UPDATE ON studio_verifications
        BEGIN SELECT RAISE(ABORT, 'immutable verification report'); END""",
)

V008 = (
    """CREATE TABLE studio_approvals (
        approval_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, build_id TEXT NOT NULL,
        revision_id TEXT NOT NULL, package_hash TEXT NOT NULL, report_id TEXT NOT NULL,
        risk TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, external_authorized INTEGER NOT NULL,
        created_at TEXT NOT NULL, revoked_at TEXT)""",
    """CREATE TABLE studio_releases (
        release_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, build_id TEXT NOT NULL,
        approval_id TEXT NOT NULL, target_root TEXT NOT NULL, status TEXT NOT NULL,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
)

V009 = (
    """CREATE TABLE studio_project_settings (
    project_id TEXT PRIMARY KEY REFERENCES studio_projects(project_id), settings_json TEXT NOT NULL)""",
)

V010 = (
    """CREATE TABLE studio_evidence_decisions (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT UNIQUE NOT NULL,
        project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        evidence_id TEXT NOT NULL, basis_hash TEXT NOT NULL, basis_json TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('confirm','revoke')),
        target_id TEXT REFERENCES studio_evidence_decisions(decision_id),
        actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
        CHECK((action='confirm' AND target_id IS NULL) OR (action='revoke' AND target_id IS NOT NULL)))""",
    "CREATE INDEX studio_evidence_decisions_project ON studio_evidence_decisions(project_id,sequence)",
    "CREATE UNIQUE INDEX one_evidence_revocation ON studio_evidence_decisions(target_id) WHERE action='revoke'",
    """CREATE TRIGGER immutable_evidence_decision BEFORE UPDATE ON studio_evidence_decisions
        BEGIN SELECT RAISE(ABORT, 'immutable evidence decision'); END""",
    """CREATE TRIGGER retain_evidence_decision BEFORE DELETE ON studio_evidence_decisions
        BEGIN SELECT RAISE(ABORT, 'retain evidence decision'); END""",
)

V011 = (
    """CREATE TABLE studio_tool_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, tool_id TEXT NOT NULL, version TEXT NOT NULL,
        action TEXT NOT NULL, contract_hash TEXT NOT NULL, record_version INTEGER NOT NULL,
        payload_json TEXT NOT NULL, actor TEXT, reason TEXT, created_at TEXT NOT NULL,
        FOREIGN KEY(tool_id,version) REFERENCES studio_tools(tool_id,version))""",
    "CREATE INDEX studio_tool_events_identity ON studio_tool_events(tool_id,version,sequence)",
    """INSERT INTO studio_tool_events(tool_id,version,action,contract_hash,record_version,payload_json,actor,reason,created_at)
        SELECT tool_id,version,'snapshot',contract_hash,record_version,
        '{"status":"' || status || '","trust_status":"' || trust_status || '"}',
        NULL,'Upgrade snapshot; original decisions remain in approval history',CURRENT_TIMESTAMP FROM studio_tools""",
    """CREATE TRIGGER immutable_tool_event BEFORE UPDATE ON studio_tool_events
        BEGIN SELECT RAISE(ABORT, 'immutable tool event'); END""",
    """CREATE TRIGGER retain_tool_event BEFORE DELETE ON studio_tool_events
        BEGIN SELECT RAISE(ABORT, 'retain tool event'); END""",
)

V012 = (
    """CREATE TABLE studio_handler_templates (
        tool_id TEXT NOT NULL, version TEXT NOT NULL, template_hash TEXT NOT NULL,
        parameters_json TEXT NOT NULL, origin_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(tool_id,version), FOREIGN KEY(tool_id,version) REFERENCES studio_tools(tool_id,version))""",
    """CREATE TRIGGER immutable_handler_template BEFORE UPDATE ON studio_handler_templates
        BEGIN SELECT RAISE(ABORT, 'immutable handler template'); END""",
    """CREATE TRIGGER retain_handler_template BEFORE DELETE ON studio_handler_templates
        BEGIN SELECT RAISE(ABORT, 'retain handler template'); END""",
)

V013 = (
    """CREATE TABLE studio_build_reviews (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT UNIQUE NOT NULL,
        project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        build_id TEXT NOT NULL REFERENCES studio_builds(build_id), package_hash TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('submit','return','archive','restore','approve','revoke')),
        report_id TEXT, approval_id TEXT, actor TEXT, reason TEXT, created_at TEXT NOT NULL)""",
    "CREATE INDEX studio_build_reviews_build ON studio_build_reviews(project_id,build_id,sequence)",
    """CREATE TRIGGER immutable_build_review BEFORE UPDATE ON studio_build_reviews
        BEGIN SELECT RAISE(ABORT, 'immutable build review'); END""",
    """CREATE TRIGGER retain_build_review BEFORE DELETE ON studio_build_reviews
        BEGIN SELECT RAISE(ABORT, 'retain build review'); END""",
)

V014 = (
    """CREATE TABLE studio_release_reconciliations (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
        release_id TEXT NOT NULL REFERENCES studio_releases(release_id),
        observation_hash TEXT NOT NULL, observation_json TEXT NOT NULL,
        outcome TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX studio_release_reconciliations_release ON studio_release_reconciliations(project_id,release_id,sequence)",
    """CREATE TRIGGER immutable_release_reconciliation BEFORE UPDATE ON studio_release_reconciliations
        BEGIN SELECT RAISE(ABORT, 'immutable release reconciliation'); END""",
    """CREATE TRIGGER retain_release_reconciliation BEFORE DELETE ON studio_release_reconciliations
        BEGIN SELECT RAISE(ABORT, 'retain release reconciliation'); END""",
)

V015 = (
    """CREATE TABLE studio_interviews (
        session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE REFERENCES studio_projects(project_id))""",
    """CREATE TABLE studio_interview_revisions (
        session_id TEXT NOT NULL REFERENCES studio_interviews(session_id), revision INTEGER NOT NULL,
        request_id TEXT NOT NULL, request_hash TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(session_id, revision), UNIQUE(session_id,request_id))""",
    """CREATE TRIGGER immutable_interview_snapshot BEFORE UPDATE ON studio_interview_revisions
        BEGIN SELECT RAISE(ABORT,'immutable interview revision'); END""",
    """CREATE TRIGGER retain_interview_snapshot BEFORE DELETE ON studio_interview_revisions
        BEGIN SELECT RAISE(ABORT,'retain interview revision'); END""",
)

V016 = (
    """CREATE TABLE studio_domain_packs (
        ref TEXT PRIMARY KEY, manifest BLOB NOT NULL, manifest_hash TEXT NOT NULL,
        source_hash TEXT NOT NULL, archive BLOB NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('candidate','active','disabled','deprecated','archived')),
        approved INTEGER NOT NULL CHECK(approved IN (0,1)), revision INTEGER NOT NULL CHECK(revision>0),
        created_at TEXT NOT NULL, CHECK(status<>'active' OR approved=1))""",
    """CREATE TRIGGER immutable_domain_pack BEFORE UPDATE OF ref,manifest,manifest_hash,source_hash,archive,created_at
        ON studio_domain_packs BEGIN SELECT RAISE(ABORT,'immutable Pack version'); END""",
    """CREATE TABLE studio_domain_assets (
        kind TEXT NOT NULL, ref TEXT NOT NULL, body BLOB NOT NULL, content_hash TEXT NOT NULL,
        PRIMARY KEY(kind,ref))""",
    """CREATE TRIGGER immutable_domain_asset BEFORE UPDATE ON studio_domain_assets
        BEGIN SELECT RAISE(ABORT,'immutable asset version'); END""",
    """CREATE TABLE studio_pattern_records (
        ref TEXT PRIMARY KEY, status TEXT NOT NULL
        CHECK(status IN ('candidate','active','disabled','deprecated','archived')),
        revision INTEGER NOT NULL CHECK(revision>0), qualification_hash TEXT,
        CHECK(status<>'active' OR qualification_hash IS NOT NULL))""",
    """CREATE TABLE studio_pattern_trials (
        pattern_ref TEXT NOT NULL REFERENCES studio_pattern_records(ref), pattern_hash TEXT NOT NULL,
        report_hash TEXT NOT NULL, report BLOB NOT NULL, status TEXT NOT NULL CHECK(status IN ('PASS','FAIL')),
        PRIMARY KEY(pattern_ref,report_hash))""",
    """CREATE TRIGGER immutable_pattern_trial BEFORE UPDATE ON studio_pattern_trials
        BEGIN SELECT RAISE(ABORT,'immutable Pattern trial'); END""",
    """CREATE TABLE studio_pack_members (
        pack_ref TEXT NOT NULL REFERENCES studio_domain_packs(ref),
        kind TEXT NOT NULL, asset_ref TEXT NOT NULL, source_hash TEXT NOT NULL,
        FOREIGN KEY(kind,asset_ref) REFERENCES studio_domain_assets(kind,ref),
        PRIMARY KEY(pack_ref,kind,asset_ref))""",
    "CREATE INDEX studio_pack_member_lookup ON studio_pack_members(kind,asset_ref,pack_ref)",
    """CREATE TABLE studio_pack_dependencies (
        pack_ref TEXT NOT NULL REFERENCES studio_domain_packs(ref),
        dependency_ref TEXT NOT NULL, PRIMARY KEY(pack_ref,dependency_ref))""",
    "CREATE INDEX studio_pack_dependency_lookup ON studio_pack_dependencies(dependency_ref,pack_ref)",
    """CREATE TABLE studio_pattern_requirements (
        pattern_ref TEXT NOT NULL, requirement_id TEXT NOT NULL, capability_id TEXT NOT NULL,
        capability_version TEXT, required INTEGER NOT NULL CHECK(required IN (0,1)),
        PRIMARY KEY(pattern_ref,requirement_id))""",
    "CREATE INDEX studio_pattern_capability_lookup ON studio_pattern_requirements(capability_id,pattern_ref)",
    """CREATE TABLE studio_pattern_pins (
        workflow_ref TEXT NOT NULL, blueprint_hash TEXT NOT NULL,
        pattern_ref TEXT NOT NULL REFERENCES studio_pattern_records(ref),
        pattern_hash TEXT NOT NULL, pack_ref TEXT NOT NULL REFERENCES studio_domain_packs(ref),
        created_at TEXT NOT NULL, PRIMARY KEY(workflow_ref,blueprint_hash,pattern_ref))""",
    "CREATE INDEX studio_pattern_dependent_lookup ON studio_pattern_pins(pattern_ref,workflow_ref)",
    """CREATE TABLE studio_domain_events (
        sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
        ref TEXT NOT NULL, action TEXT NOT NULL, content_hash TEXT NOT NULL, revision INTEGER NOT NULL,
        actor TEXT, reason TEXT, review_channel TEXT, decision_hash TEXT, created_at TEXT NOT NULL)""",
    "CREATE INDEX studio_domain_event_history ON studio_domain_events(kind,ref,sequence)",
    """CREATE TRIGGER immutable_domain_event BEFORE UPDATE ON studio_domain_events
        BEGIN SELECT RAISE(ABORT,'immutable domain event'); END""",
    """CREATE TRIGGER retain_domain_event BEFORE DELETE ON studio_domain_events
        BEGIN SELECT RAISE(ABORT,'retain domain event'); END""",
)


V017 = (
    """CREATE TABLE studio_pattern_extractions (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
        project_revision INTEGER NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
        report_hash TEXT NOT NULL, artifact BLOB NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(project_id,request_id))""",
    """CREATE TRIGGER immutable_pattern_extraction BEFORE UPDATE ON studio_pattern_extractions
        BEGIN SELECT RAISE(ABORT,'immutable extraction draft'); END""",
    """CREATE TRIGGER retain_pattern_extraction BEFORE DELETE ON studio_pattern_extractions
        BEGIN SELECT RAISE(ABORT,'retain extraction draft'); END""",
)


V018 = (
    """CREATE TABLE studio_pattern_improvements (
        id TEXT PRIMARY KEY, source_ref TEXT NOT NULL, target_ref TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE studio_pattern_improvement_revisions (
        proposal_id TEXT NOT NULL REFERENCES studio_pattern_improvements(id),
        revision INTEGER NOT NULL, body BLOB NOT NULL, PRIMARY KEY(proposal_id,revision))""",
    """CREATE TRIGGER immutable_pattern_improvement BEFORE UPDATE ON studio_pattern_improvement_revisions
        BEGIN SELECT RAISE(ABORT,'immutable improvement revision'); END""",
    """CREATE TRIGGER retain_pattern_improvement BEFORE DELETE ON studio_pattern_improvement_revisions
        BEGIN SELECT RAISE(ABORT,'retain improvement history'); END""",
)


MIGRATIONS = {
    1: V001,
    2: V002,
    3: V003,
    4: V004,
    5: V005,
    6: V006,
    7: V007,
    8: V008,
    9: V009,
    10: V010,
    11: V011,
    12: V012,
    13: V013,
    14: V014,
    15: V015,
    16: V016,
    17: V017,
    18: V018,
}

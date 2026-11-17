"""Ordered, transactional Studio schema migrations."""

APPLICATION_ID = 0x53545544  # STUD; rejects accidentally selected Runtime databases.
SCHEMA_VERSION = 6

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

MIGRATIONS = {1: V001, 2: V002, 3: V003, 4: V004, 5: V005, 6: V006}

"""Ordered, transactional Studio schema migrations."""

APPLICATION_ID = 0x53545544  # STUD; rejects accidentally selected Runtime databases.
SCHEMA_VERSION = 1

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

MIGRATIONS = {1: V001}

"""Immutable quality assessments, user feedback, previews and profile revisions."""

import hashlib

VERSION = 5
TABLES = {
    "graph_assessments",
    "graph_feedback",
    "graph_previews",
    "graph_profiles",
    "graph_published",
}
STATEMENTS = (
    """CREATE TABLE graph_assessments(
        id TEXT PRIMARY KEY, workflow TEXT NOT NULL, session_id TEXT NOT NULL, graph_hash TEXT NOT NULL,
        original_hash TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX graph_assessment_session ON graph_assessments(session_id)",
    """CREATE TABLE graph_feedback(
        id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES graph_assessments(id),
        revision INTEGER NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL, body TEXT NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(assessment_id,revision), UNIQUE(assessment_id,request_id))""",
    """CREATE TABLE graph_previews(
        run_id TEXT PRIMARY KEY, assessment_id TEXT, path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL)""",
    """CREATE TABLE graph_published(
        run_id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES graph_assessments(id), revision INTEGER NOT NULL,
        output_hash TEXT NOT NULL)""",
    """CREATE TABLE graph_profiles(
        workflow TEXT NOT NULL, profile_id TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
        body TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(workflow,profile_id,revision))""",
    *(
        f"CREATE TRIGGER immutable_{t} BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT,'immutable quality record'); END"
        for t in sorted(TABLES)
    ),
    *(
        f"CREATE TRIGGER retain_{t} BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT,'retain quality history'); END"
        for t in sorted(TABLES)
    ),
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def apply(db):
    for statement in STATEMENTS:
        db.execute(statement)

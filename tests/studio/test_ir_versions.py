import pytest
from ir_cases import tools, valid_ir
from workflow_studio.contracts import StudioError
from workflow_studio.ir.validator import validate_ir
from workflow_studio.ir.versioning import ir_fingerprint
from workflow_studio.service import StudioService

pytest.importorskip("PIL", reason="IR persistence fixture imports a source in Studio environment")


def test_ir_roundtrip_fingerprint_and_conflict(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create("IR")
        source = studio.sources.import_text(p.project_id, 1, "複製檔案")
        payload = valid_ir()
        payload["evidence"][0]["source_id"] = source.source_id
        revision = studio.projects.save_ir(p.project_id, 2, payload, tools())
        assert revision.status == "ir_ready"
        assert revision.ir_hash == ir_fingerprint(validate_ir(payload, tools()))
        with pytest.raises(StudioError) as caught:
            studio.projects.save_ir(p.project_id, 2, payload, tools())
        assert caught.value.code == "REVISION_CONFLICT"
        with pytest.raises(StudioError, match="IR 預期版本"):
            studio.projects.save_ir(p.project_id, 3, payload, tools())
        payload["revision"] = 2
        newer = studio.projects.save_ir(p.project_id, 3, payload, tools())
        assert newer.parent_id == revision.revision_id
    with StudioService.open_workspace(tmp_path) as studio:
        assert studio.projects.get_revision(p.project_id, revision.revision_id) == revision


def test_invalid_ir_and_foreign_evidence_do_not_create_revision(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create("IR")
        payload = valid_ir()
        with pytest.raises(StudioError) as caught:
            studio.projects.save_ir(p.project_id, 1, payload, tools())
        assert caught.value.code == "EVIDENCE_INSUFFICIENT"
        assert studio.projects.revisions(p.project_id) == []
        assert studio.projects.get(p.project_id).version == 1

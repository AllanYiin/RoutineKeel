from workflow_studio.ir.models import Port, ToolSignature


def tools():
    return {
        "file.copy@1.0.0": ToolSignature(
            tool_id="file.copy",
            version="1.0.0",
            kind="deterministic",
            inputs={"source": Port(type="file")},
            outputs={"result": Port(type="file")},
            platforms=["windows", "macos", "linux"],
            side_effects="local_write",
        )
    }


def valid_ir():
    return {
        "ir_version": "1.0",
        "workflow_id": "copy-example",
        "revision": 1,
        "goal": {"description": "完整複製輸入檔案", "success_criteria": ["輸出內容與輸入相同"]},
        "inputs": {"source": {"type": "file"}},
        "secrets": [],
        "evidence": [
            {
                "id": "description",
                "source_id": "source_fixture",
                "source_version": 1,
                "evidence_locator": "說明.txt:1",
                "claim": "複製檔案",
                "confidence_label": "observed",
            }
        ],
        "outputs": {"result": "artifact://copy/result"},
        "nodes": [
            {
                "id": "copy",
                "kind": "deterministic",
                "tool": "file.copy",
                "tool_version": "1.0.0",
                "inputs": {"source": {"source": "input://source", "port": {"type": "file"}}},
                "outputs": {"result": {"type": "file", "path": "artifacts/result.txt"}},
                "depends_on": [],
                "parameters": {},
                "preconditions": [{"operator": "exists", "reference": "input://source"}],
                "postconditions": [{"operator": "exists", "reference": "artifact://copy/result"}],
                "side_effects": "local_write",
                "retry_policy": {"mode": "never", "max_attempts": 1},
                "cache_policy": {
                    "mode": "content_addressed",
                    "fingerprint_fields": ["inputs", "parameters", "tool_version"],
                },
                "platform_requirements": ["windows", "macos", "linux"],
                "resources": {"memory_mb": 64, "disk_mb": 10, "network": False},
                "timeout_seconds": 60,
                "evidence_refs": ["description"],
            }
        ],
    }

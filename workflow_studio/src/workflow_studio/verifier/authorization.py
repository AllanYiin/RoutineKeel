from dataclasses import dataclass, field

from ..compiler.generator import Compiler
from ..compiler.package import digest
from ..contracts import StudioError
from ..project.models import canonical_json
from .golden_cases import GoldenCases


@dataclass(frozen=True)
class RuntimeAuthorization:
    request_hash: str
    secrets: dict[str, str] = field(repr=False)


def disclosure(studio, project_id, build_id, cases):
    from ..governance.reviews import ReviewStore

    ReviewStore(studio.repository).assert_open(project_id, build_id)
    build = Compiler(studio).get(project_id, build_id)
    ir = studio.projects.get_revision(project_id, build["revision_id"]).ir
    models = [
        {
            "node_id": node["id"],
            "endpoint": node["parameters"]["endpoint"],
            "model": node["llm_policy"]["model"],
            "secret": node["llm_policy"]["api_key"],
            "budget_micro_usd": node["llm_policy"]["budget_micro_usd"],
        }
        for node in ir["nodes"]
        if node["kind"] == "llm"
    ]
    sources = []
    for binding in cases:
        case = GoldenCases(studio).get(project_id, binding["case_id"], binding["version"])
        sources.append(
            {
                "name": case["specification"]["name"],
                "input": case["specification"]["input"],
                "binding": dict(binding),
                "acceptance_hash": digest(canonical_json(case["specification"]).encode()),
            }
        )
    result = {
        "project_id": project_id,
        "build_id": build_id,
        "package_hash": build["package_hash"],
        "ir_hash": build["mapping"]["ir_hash"],
        "models": models,
        "sources": sources,
        "maximum_budget_micro_usd": len(cases) * sum(m["budget_micro_usd"] for m in models),
    }
    result["request_hash"] = digest(canonical_json(result).encode())
    return result


def environment(disclosed, authorization):
    if not disclosed["models"]:
        return {}
    if authorization is None or authorization.request_hash != disclosed["request_hash"]:
        raise StudioError("APPROVAL_REQUIRED", "LLM 試做須先核准模型、來源版本與最大預算。")
    result = {"WORKFLOW_STUDIO_LLM_AUTHORIZATION": disclosed["ir_hash"]}
    for model in disclosed["models"]:
        symbolic = model["secret"].split("://", 1)[1]
        key = authorization.secrets.get(symbolic)
        if not isinstance(key, str) or not key:
            raise StudioError("PROVIDER_UNAVAILABLE", "缺少已核准模型對應的憑證。")
        result["WORKFLOW_STUDIO_SECRET_" + symbolic.upper().replace("-", "_")] = key
    return result

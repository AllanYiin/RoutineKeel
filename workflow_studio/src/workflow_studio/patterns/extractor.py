"""Extract an inert subgraph and preserve explicit obligations at its boundary."""

import hashlib
import io
import json
import zipfile
from uuid import uuid4

from pydantic import Field, JsonValue, ValidationError
from routinekeel_contracts.asset_loading import load_asset_bytes
from routinekeel_contracts.domain_assets import (
    AssetID,
    AssetRef,
    AssetVersion,
    CapabilityRequirement,
    Fingerprint,
    Label,
    Locus,
    canonical_json,
    content_hash,
)
from routinekeel_contracts.graph_validator import port_for, reference, validate_blueprint
from routinekeel_contracts.models import Contract, Identifier
from routinekeel_contracts.privacy import contains_private_data

from ..contracts import StudioError
from ..project.service import now


class SourceEvidence(Contract):
    provider: str | None
    fingerprint: Fingerprint


class CapabilityMapping(Contract):
    capability: AssetID
    version: AssetVersion
    required: bool = True
    pinned_adapter: AssetRef | None = None
    fallback_contract: AssetID | None = None
    execution_loci: list[Locus] = Field(default_factory=list)


class ExtractionRequest(Contract):
    workflow_ref: str
    selected_nodes: list[Identifier] = Field(min_length=1, max_length=401)
    purpose: Label
    pattern_id: AssetID
    version: AssetVersion = "1.0.0"
    expected_revision: int = Field(ge=1)
    request_id: Label
    capability_map: dict[str, CapabilityMapping] = Field(default_factory=dict)
    portable_parameters: dict[str, list[str]] = Field(default_factory=dict)


class ExtractionSource(Contract):
    graph: dict[str, JsonValue]
    blueprint_sha256: Fingerprint
    wheel_sha256: Fingerprint
    dependency_evidence: dict[str, SourceEvidence]


def _node_references(node):
    yield from (binding["source"] for binding in node["inputs"].values())
    kind = node["kind"]
    if kind == "switch":
        yield node["condition"]["source"]
    elif kind == "join":
        for group in node["branches"].values():
            yield from group.values()
        yield from node["fallbacks"].values()
    elif kind == "foreach":
        yield node["items"]
    elif kind == "repeat_until":
        yield node["initial"]["source"]


def _remap_inputs(node, change):
    for binding in node["inputs"].values():
        binding["source"] = change(binding["source"])
    kind = node["kind"]
    if kind == "switch":
        node["condition"]["source"] = change(node["condition"]["source"])
    elif kind == "join":
        node["branches"] = {
            route: {port: change(value) for port, value in bindings.items()}
            for route, bindings in node["branches"].items()
        }
        node["fallbacks"] = {key: change(value) for key, value in node["fallbacks"].items()}
    elif kind == "foreach":
        node["items"] = change(node["items"])
    elif kind == "repeat_until":
        node["initial"]["source"] = change(node["initial"]["source"])


def _controls(nodes, selected, guards, report):
    incoming = {}
    outgoing = {}
    for name, node in nodes.items():
        for source in node["depends_on"]:
            if name in selected and source not in selected:
                incoming.setdefault((source, None), []).append(name)
            elif source in selected and name not in selected:
                outgoing.setdefault((name, None), []).append(source)
        if name in selected:
            node["depends_on"] = [parent for parent in node["depends_on"] if parent in selected]
            for switch, route in guards[name].items():
                if switch not in selected:
                    incoming.setdefault((switch, route), []).append(name)
        if node["kind"] == "switch" and name in selected:
            for label, targets in node["routes"].items():
                for target in set(targets) - selected:
                    outgoing.setdefault((target, label), []).append(name)
                    report["issues"].append(
                        {
                            "code": "PATTERN_GRAPH_INVALID",
                            "node": name,
                            "route": label,
                            "reason": "選區須包含這個條件的目標節點以保留選路語意。",
                        }
                    )

    def ports(edges, stem):
        result = {}
        for index, ((external, route), local_nodes) in enumerate(
            sorted(edges.items(), key=lambda item: (item[0][0], item[0][1] or "")), 1
        ):
            key = f"{stem}_{index}"
            result[key] = {"kind": "route" if route else "order", "nodes": sorted(set(local_nodes))}
            if route:
                result[key]["route"] = route
            report["control_bindings"][key] = {
                "external_node_hash": content_hash(external),
                **result[key],
            }
        return result

    return ports(incoming, "control_in"), ports(outgoing, "control_out")


def _portable_graph_node(node, location, request, source, requirements, report):
    node["evidence"] = [content_hash(value) for value in node["evidence"]]
    if node["kind"] == "human_review" and contains_private_data(node["prompt"]):
        node["prompt"] = "請確認此節點的交付。原問題需由工作流設計者補上可攜文字。"
        report["issues"].append({"code": "PORTABLE_TEXT_REVIEW", "node": location})
    if node["kind"] in {"foreach", "repeat_until"}:
        for child in node["body"]["nodes"]:
            _portable_graph_node(
                child, f"{location}/{child['id']}", request, source, requirements, report
            )
    if node["kind"] != "task":
        return
    observed = source.dependency_evidence.get(location)
    provider = observed.provider if observed else None
    mapping = request.capability_map.get(location)
    raw_requirement = node.pop("capability_requirement", None)
    declared = CapabilityRequirement.model_validate(raw_requirement) if raw_requirement else None
    if (
        mapping is None
        and declared is None
        and provider == "builtin"
        and node["capability"] in {"text.copy", "text.upper", "json.identity"}
    ):
        mapping = CapabilityMapping(capability=node["capability"], version="1.0.0")
    origin_hash = content_hash([node["capability"], node["capability_version"]])
    locus = {
        "desktop_host": "host_mediated",
        "builtin": "local_runtime",
        "native": "local_runtime",
    }.get(provider)
    rule_id = f"requirement_{len(requirements) + 1}"
    if mapping is None and declared is not None:
        capability = declared.capability
        version = node["capability_version"]
        if version.isdecimal():
            version += ".0.0"
        portability = declared.portability
    elif mapping is None:
        capability = f"unresolved.{rule_id}"
        version = "0.0.0"
        portability = "unresolved"
        report["issues"].append({"code": "CAPABILITY_UNRESOLVED", "node": location})
    else:
        capability = mapping.capability
        version = mapping.version
        portability = "pinned" if mapping.pinned_adapter else "flexible"
    pair = (capability, version)
    options = (
        mapping.model_dump(exclude={"capability", "version"}) if mapping else {"required": True}
    )
    declared_id = requirements[pair].id if pair in requirements else rule_id
    if mapping is None and declared is not None:
        rule = declared.model_copy(update={"id": declared_id})
    else:
        rule = CapabilityRequirement(
            id=declared_id,
            capability=capability,
            version=version,
            portability=portability,
            **options,
        )
    if pair not in requirements:
        requirements[pair] = rule
    elif requirements[pair] != rule:
        report["issues"].append({"code": "PATTERN_DEPENDENCY_CONFLICT", "node": location})
    report["dependencies"].append(
        {
            "node": location,
            "source_capability_hash": origin_hash,
            "source_binding_hash": observed.fingerprint if observed else None,
            "source_locus": locus,
            "capability": capability,
            "requirement": requirements[pair].id,
            "required": rule.required,
            "portability": portability,
            "status": "unresolved" if portability == "unresolved" else "needs_qualification",
        }
    )
    node["capability"] = capability
    node["capability_version"] = version
    chosen = request.portable_parameters.get(location, [])
    parameters = {}
    for key, value in node["parameters"].items():
        if key in chosen and not contains_private_data({key: value}):
            parameters[key] = value
        else:
            report["removed_artifact_hashes"].append(content_hash([location, key, value]))
            report["issues"].append({"code": "PARAMETER_REVIEW_REQUIRED", "node": location})
    if set(chosen) - node["parameters"].keys():
        report["issues"].append({"code": "UNKNOWN_PORTABLE_PARAMETER", "node": location})
    node["parameters"] = parameters
    if contains_private_data(node["permissions"]):
        node["permissions"] = []
        report["issues"].append({"code": "PERMISSIONS_REVIEW_REQUIRED", "node": location})


def extract(request, source):
    guards = {}
    graph, _ = validate_blueprint(source.graph, route_guards=guards)
    workflow = graph.workflow
    if request.workflow_ref != f"{workflow.id}@{workflow.version}":
        raise StudioError("CONTRACT_MISMATCH", "擷取來源與指定工作流版本不同。")
    native_nodes = {node.id: node for node in graph.nodes}
    selected = set(request.selected_nodes)
    if len(selected) != len(request.selected_nodes) or not selected <= native_nodes.keys():
        raise StudioError("PATTERN_GRAPH_INVALID", "選區含有未知或重複的節點。")
    nodes = {node.id: node.model_dump(mode="json") for node in graph.nodes}
    report = {
        "status": "draft_only",
        "workflow_ref": request.workflow_ref,
        "source_blueprint_hash": source.blueprint_sha256,
        "source_wheel_hash": source.wheel_sha256,
        "source_graph_hash": content_hash(source.graph),
        "source_revision": workflow.revision,
        "selected_nodes": [key for key in nodes if key in selected],
        "input_mapping": {},
        "output_mapping": {},
        "control_bindings": {},
        "dependencies": [],
        "issues": [],
        "removed_artifact_hashes": [],
        "source_cases_sha256": content_hash(graph.quality_contract.get("golden_cases", [])),
        "source_quality_sha256": content_hash(graph.quality_contract),
    }
    if set(graph.quality_contract) - {"golden_cases"}:
        report["issues"].append({"code": "QUALITY_CONTRACT_REVIEW_REQUIRED"})
    control_inputs, control_outputs = _controls(nodes, selected, guards, report)
    boundary_inputs = {}
    inbound = {}
    requirements = {}

    def bind(value):
        ref = reference(value)
        if ref.scheme == "artifact" and ref.owner in selected:
            return value
        if value not in inbound:
            port_id = f"in_{len(inbound) + 1}"
            inbound[value] = port_id
            boundary_inputs[port_id] = port_for(graph, native_nodes, value).model_dump(mode="json")
            report["input_mapping"][port_id] = value
        return f"input://{inbound[value]}"

    outbound = {}
    boundary_outputs = {}

    def expose(value, name=None):
        ref = reference(value)
        if ref.scheme != "artifact" or ref.owner not in selected:
            return
        if name is None:
            if value in outbound.values():
                return
            name = next(
                f"out_{i}" for i in range(1, len(outbound) + 2) if f"out_{i}" not in outbound
            )
        outbound[name] = value
        boundary_outputs[name] = port_for(graph, native_nodes, value).model_dump(mode="json")
        report["output_mapping"][name] = value

    for name, value in graph.outputs.items():
        expose(value, name)
    for name, node in nodes.items():
        if name not in selected:
            for value in _node_references(node):
                expose(value)
        else:
            _remap_inputs(node, bind)
            try:
                _portable_graph_node(node, name, request, source, requirements, report)
            except ValidationError:
                report["issues"].append({"code": "PATTERN_DEPENDENCY_CONFLICT", "node": name})
    known_tasks = {item["node"] for item in report["dependencies"]}
    if (request.capability_map.keys() | request.portable_parameters.keys()) - known_tasks:
        report["issues"].append(
            {
                "code": "PATTERN_DEPENDENCY_CONFLICT",
                "reason": "能力或參數對照包含未選取的工作節點。",
            }
        )
    title = request.purpose
    if contains_private_data(title):
        title = "待補可攜用途描述"
        report["issues"].append({"code": "PORTABLE_TEXT_REVIEW"})
    candidate = {
        "pattern_schema_version": "1.0",
        "pattern": {
            "id": request.pattern_id,
            "version": request.version,
            "title": title,
            "kind": "reusable_graph",
            "declared_domains": [request.pattern_id.split(".")[0]],
            "source": {
                "workflow_id": workflow.id,
                "version": workflow.version,
                "revision": workflow.revision,
                "graph_sha256": report["source_graph_hash"],
            },
        },
        "boundary": {
            "inputs": boundary_inputs,
            "outputs": boundary_outputs,
            "control_inputs": control_inputs,
            "control_outputs": control_outputs,
        },
        "requirements": [value.model_dump(mode="json") for value in requirements.values()],
        "triggers": {
            "design": {
                "activation": "suggest",
                "task_categories": [],
                "intents": [],
                "positive_examples": [],
                "negative_examples": [],
                "prerequisites": [],
            },
            "runtime": {"activation": "compiled_graph_only"},
            "evolution": {"signals": []},
        },
        "graph": {
            "inputs": boundary_inputs,
            "outputs": outbound,
            "nodes": [node for name, node in nodes.items() if name in selected],
        },
        "tests": {"golden_cases_manifest": "cases/index.json", "required_edges": ["normal"]},
    }
    try:
        if any(
            issue["code"] in {"PATTERN_GRAPH_INVALID", "PATTERN_DEPENDENCY_CONFLICT"}
            for issue in report["issues"]
        ):
            raise StudioError("PATTERN_GRAPH_INVALID", "選區的選路邊界不完整。")
        if contains_private_data(candidate):
            raise StudioError("SECRET_IN_PORTABLE_PACKAGE", "可攜候選仍含不合規資料。")
        loaded = load_asset_bytes("pattern", canonical_json(candidate))
    except (StudioError, ValidationError) as error:
        report["status"] = "blocked"
        code = error.code if isinstance(error, StudioError) else "PATTERN_GRAPH_INVALID"
        if code != "SECRET_IN_PORTABLE_PACKAGE":
            code = "PATTERN_GRAPH_INVALID"
        report["issues"].append({"code": code, "reason": "子圖的控制或資料邊界需補齊。"})
        return {"pattern": None, "pattern_hash": None, "report": report}
    return {
        "pattern": json.loads(loaded.canonical),
        "pattern_hash": loaded.sha256,
        "report": report,
    }


def save_extraction(studio, project_id, source, parameters):
    try:
        request = ExtractionRequest.model_validate(parameters)
        origin = ExtractionSource.model_validate(source)
    except ValidationError as error:
        raise StudioError("INPUT_INVALID", "擷取的版本、選區或來源證據格式不符。") from error
    fingerprint = content_hash([request.model_dump(mode="json"), origin.model_dump(mode="json")])
    with studio.repository.transaction() as db:
        prior = db.execute(
            """SELECT request_hash,artifact FROM studio_pattern_extractions
            WHERE project_id=? AND request_id=?""",
            (project_id, request.request_id),
        ).fetchone()
        if prior:
            if prior["request_hash"] != fingerprint:
                raise StudioError("REVISION_CONFLICT", "擷取請求已使用不同來源或選區。")
            return json.loads(prior["artifact"])
        project = studio.projects.get(project_id)
        if project.version != request.expected_revision or project.status != "active":
            raise StudioError("REVISION_CONFLICT", "Studio 專案已更改或封存，請重新確認選區。")
        result = extract(request, origin)
        result["extraction_id"] = f"extraction_{uuid4().hex}"
        result["report_hash"] = content_hash(result["report"])
        db.execute(
            "INSERT INTO studio_pattern_extractions VALUES (?,?,?,?,?,?,?,?)",
            (
                result["extraction_id"],
                project_id,
                project.version,
                request.request_id,
                fingerprint,
                result["report_hash"],
                canonical_json(result),
                now(),
            ),
        )
    return result


def get_extraction(studio, project_id, extraction_id):
    result = studio.repository.connection.execute(
        "SELECT report_hash,artifact FROM studio_pattern_extractions WHERE id=? AND project_id=?",
        (extraction_id, project_id),
    ).fetchone()
    if result is None:
        raise StudioError("DOMAIN_ASSET_UNKNOWN", "沒有這個專案的擷取草稿。")
    artifact = json.loads(result["artifact"])
    if content_hash(artifact["report"]) != result["report_hash"] or (
        artifact["pattern"] is not None
        and content_hash(artifact["pattern"]) != artifact["pattern_hash"]
    ):
        raise StudioError("CONTRACT_MISMATCH", "擷取報告指紋不符。")
    return artifact


def export_extraction(studio, workspace, project_id, extraction_id):
    from ..conversation.blueprints import materialize

    artifact = get_extraction(studio, project_id, extraction_id)
    if artifact["pattern"] is None:
        raise StudioError("PATTERN_GRAPH_INVALID", "擷取草稿的邊界仍需補齊。")
    if contains_private_data(artifact):
        raise StudioError("SECRET_IN_PORTABLE_PACKAGE", "草稿包含需要審閱的私密資料。")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, body in (
            ("pattern.json", artifact["pattern"]),
            ("extraction.json", artifact["report"]),
        ):
            archive.writestr(zipfile.ZipInfo(name), canonical_json(body))
    raw = data.getvalue()
    path = materialize(workspace, "pattern_drafts", raw)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "pattern_hash": artifact["pattern_hash"],
        "status": "draft_only",
    }

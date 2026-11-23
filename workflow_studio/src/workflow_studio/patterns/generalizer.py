"""Keep source contracts immutable while deriving a common Pattern or a variant."""

import copy

from routinekeel_contracts.asset_loading import load_asset_bytes
from routinekeel_contracts.domain_assets import (
    ProfileSchema,
    canonical_json,
    content_hash,
    profile_value_matches,
)
from routinekeel_contracts.privacy import contains_private_data

from ..contracts import StudioError


def _lift_profile_parameters(models, schema_payload, mappings):
    schema = ProfileSchema.model_validate(schema_payload)
    if not isinstance(mappings, dict) or not mappings:
        raise StudioError("INPUT_INVALID", "請指定 Profile 欄位和來源參數的對應。")
    for slot, field in mappings.items():
        node_id, separator, parameter = slot.partition("/")
        field_schema = schema.fields.get(field)
        if not separator or field_schema is None:
            raise StudioError("INPUT_INVALID", "Profile 對應需要有效的節點參數和欄位。")
        for model in models:
            node = next((node for node in model["graph"]["nodes"] if node["id"] == node_id), None)
            parameters = node.get("parameters", {}) if node else {}
            if parameter not in parameters:
                raise StudioError("PATTERN_DEPENDENCY_CONFLICT", "來源缺少可通用化的參數。")
            value = parameters.pop(parameter)
            if not profile_value_matches(field_schema, value) or contains_private_data(
                {field: value}
            ):
                raise StudioError(
                    "PATTERN_DEPENDENCY_CONFLICT", "來源參數與 Profile Schema 不相容。"
                )
            if schema.ref not in model["profile_schema_refs"]:
                model["profile_schema_refs"].append(schema.ref)
            model["profile_parameters"].append(
                {
                    "profile_schema": schema.ref,
                    "field": field,
                    "node": node_id,
                    "parameter": parameter,
                }
            )


def generalize(
    sources, *, pattern_id, version, variant=None, title=None, profile=None, profile_mappings=None
):
    if not isinstance(sources, list) or not 2 <= len(sources) <= 20:
        raise StudioError("INPUT_INVALID", "請提供 2–20 個可比較的 Pattern 版本。")
    loaded = [load_asset_bytes("pattern", canonical_json(source)) for source in sources]
    if len({asset.model.pattern.ref for asset in loaded}) != len(sources):
        raise StudioError("INPUT_INVALID", "來源須為不同的 Pattern 版本。")
    models = [asset.model.model_dump(mode="json", by_alias=True) for asset in loaded]
    if profile is not None or profile_mappings is not None:
        _lift_profile_parameters(models, profile, profile_mappings)
    if any(
        identity["id"] == pattern_id and identity["version"] == version
        for identity in (model["pattern"] for model in models)
    ):
        raise StudioError("REVISION_CONFLICT", "通用化或變體須用新的資產版本。")
    # Every field in this set affects behavior, including profile scope references,
    # permission/capability contracts and quality tolerances.
    compare = (
        "boundary",
        "requirements",
        "quality_contract",
        "profile_schema_refs",
        "profile_parameters",
        "policy_refs",
        "graph",
    )
    differing = [
        key for key in compare if any(model[key] != models[0][key] for model in models[1:])
    ]
    report = {
        "source_patterns": [
            {"ref": entry.model.pattern.ref, "hash": entry.sha256} for entry in loaded
        ],
        "different_contract_fields": differing,
        "profile_schema_refs": models[0]["profile_schema_refs"] if not differing else [],
        "status": "variant_required" if differing else "generalized_draft",
    }
    if variant is None and differing:
        return {"pattern": None, "report": report}
    if variant is not None and (type(variant) is not int or not 0 <= variant < len(models)):
        raise StudioError("INPUT_INVALID", "請指定要保留契約的來源變體。")
    selected = copy.deepcopy(models[variant or 0])
    selected["pattern"].update(id=pattern_id, version=version)
    # Multi-source provenance belongs in the report, not in a single-source field.
    selected["pattern"]["source"] = None
    if title is not None:
        selected["pattern"]["title"] = title
    if not differing and variant is None:
        # Examples and prerequisites are design guidance. Preserve all restrictions
        # and do not elevate a positive example into an automatic recommendation.
        trigger = selected["triggers"]["design"]
        for key, values in trigger.items():
            if isinstance(values, list):
                trigger[key] = list(
                    dict.fromkeys(
                        value for model in models for value in model["triggers"]["design"][key]
                    )
                )
    if contains_private_data(selected):
        raise StudioError("SECRET_IN_PORTABLE_PACKAGE", "通用化候選含需移除的私密資料。")
    candidate = load_asset_bytes("pattern", canonical_json(selected))
    report.update(
        status="variant_draft" if variant is not None else "generalized_draft",
        candidate_hash=candidate.sha256,
        profile_schema_refs=candidate.model.profile_schema_refs,
        quality_contract_hash=content_hash(candidate.model.quality_contract),
    )
    return {"pattern": candidate.model.model_dump(mode="json", by_alias=True), "report": report}

"""Admission of the host's resolution of an interview blueprint."""

from importlib.metadata import entry_points
import json
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .source import require, review_roles, validate_value
from .program import run_program, validate_program


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Example(Model):
    context: dict
    expected: dict
    expected_arguments: dict | None = None


class Recipe(Model):
    mode: Literal["host", "human", "external", "local", "handoff"]
    capability: str = ""
    operation: str = ""
    arguments: str = ""
    result: str = ""
    examples: list[Example] = Field(default_factory=list, max_length=16)
    extra_inputs: list[str] = Field(default_factory=list)
    rationale: str = ""
    reuse_approved: bool = False
    profile_config: list[str] | None = None
    assertion: str = ""
    asset_fields: list[list[str]] = Field(default_factory=list)
    text_inputs: dict[str, list[str]] = Field(default_factory=dict)


class Binding(Model):
    tool: str = Field(min_length=1)
    mapping: dict[str, str]
    evidence: str = Field(min_length=1)
    kind: Literal["host_probe", "fixture", "manual", "optional_disabled", "local_probe"]
    version: str = Field(min_length=1)
    probe: dict | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    output_mapping: dict[str, str] = Field(default_factory=dict)
    claim_required: bool = False
    executables: list[str] = Field(default_factory=list)


class Resolution(Model):
    package_version: str | None = Field(
        default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$"
    )
    steps: dict[str, Recipe]
    bindings: dict[str, Binding]
    decisions: dict[str, str]
    gaps: dict[str, str]
    # Host-approved substitutions for a delivery variant (e.g. manual publication).
    terminal_step: str | None = None
    final_outputs: list[str] | None = None
    handoff_contracts: dict[str, dict] = Field(default_factory=dict)


def local_operations():
    result = {}
    for ep in entry_points(group="routinekeel.blueprint_operations"):
        require(ep.name not in result, f"本機能力重名 {ep.name}")
        result[ep.name] = ep
    return result


def transform(program, context):
    if not program:
        return context["result"]
    require(len(program) <= 32768, "候選程式超過上限")
    return json.loads(
        run_program(
            program, json.dumps(context, ensure_ascii=False, allow_nan=False), {}
        )
    )


def compile_plan(source, resolution):
    from .source import check_schema

    r = Resolution.model_validate(resolution)
    source_version = source["manifest"]["version"]
    version = r.package_version or source_version
    require(
        tuple(map(int, version.split(".")))
        >= tuple(map(int, source_version.split("."))),
        "套件版本不可低於藍圖",
    )
    deps = source["dependencies"]
    require(
        set(r.steps) <= set(source["steps"]) and set(r.bindings) >= set(deps),
        "依賴或步驟綁定不完整",
    )
    require(
        set(r.gaps) == set(source["gaps"]) and all(v.strip() for v in r.gaps.values()),
        "每個缺口須有可查處理紀錄",
    )
    require(
        all(v.strip() for v in r.decisions.values()) and r.decisions.get("contracts"),
        "請確認產物合約與假設",
    )
    for key, dep in deps.items():
        binding = r.bindings[key]
        if dep["required"]:
            require(binding.kind != "optional_disabled", f"必要能力未具備 {key}")
        if binding.kind in {"optional_disabled", "manual"}:
            require(key in r.decisions, f"請明確選定 {key} 的替代交付方式")
    for key, binding in r.bindings.items():
        if binding.kind == "host_probe":
            probe = binding.probe or {}
            require(
                binding.mapping
                and probe.get("tool") == binding.tool
                and probe.get("version") == binding.version
                and probe.get("status") == "passed"
                and probe.get("request_id")
                and probe.get("observed"),
                f"{key} 缺少與實際工具、版本與輸出相符的宿主探測回執",
            )
            require(
                binding.input_schema and binding.output_schema,
                f"{key} 缺少工具的實際 I/O schema",
            )
        for schema in (binding.input_schema, binding.output_schema):
            if schema is not None:
                check_schema(schema)
    contracts = dict(source["contracts"])
    variant = r.terminal_step is not None
    require(
        not variant or ("delivery_mode" in r.decisions and r.final_outputs),
        "選擇替代交付必須明定驗收結果",
    )
    require(not r.handoff_contracts or variant, "替代合約需選擇交付模式")
    for key, contract in r.handoff_contracts.items():
        require(key not in contracts, "不能放寬既有合約")
        check_schema(contract["schema"])
        contracts[key] = contract
    points = source["definition"]["review"]["points"]
    after, human = review_roles(points)
    available = set(source["inputs"])
    steps = []
    operations = local_operations()
    for s in source["definition"]["steps"]:
        sid = s["id"]
        rec = r.steps.get(sid)
        require(rec is not None, f"步驟 {sid} 尚未實作")
        inputs = list(dict.fromkeys([*s["inputs"], *rec.extra_inputs]))
        require(set(inputs) <= available, "修訂依賴無法在此步取得")
        require(
            not rec.extra_inputs or rec.rationale.strip(),
            "補接依賴應記錄兩處來源與理由",
        )
        outputs = list(s["outputs"])
        if rec.mode == "handoff":
            require(
                sid == r.terminal_step
                and r.handoff_contracts
                and rec.rationale.strip(),
                "人工交付須明確定義終點",
            )
            outputs = list(r.handoff_contracts)
        require(
            not (s.get("actor") == "human" or sid in human) or rec.mode == "human",
            "人審步驟須由人決定",
        )
        if rec.mode in {"host", "external"}:
            require(
                rec.capability in r.bindings
                and r.bindings[rec.capability].kind in {"host_probe", "fixture"},
                "宿主呼叫缺少已測能力綁定",
            )
        if rec.mode == "external":
            require(
                any(n["mode"] == "human" or n["review"] for n in steps),
                "對外操作前須有人工確認",
            )
        require(
            not rec.reuse_approved or (sid in after and not steps),
            "只可重用已審核的前置設定",
        )
        if rec.operation:
            require(
                rec.operation in operations, f"請先安裝受信任的本機能力 {rec.operation}"
            )
        require(
            rec.mode not in {"local", "handoff"} or rec.result or rec.operation,
            "本機步驟缺少實作",
        )
        if rec.mode == "human":
            require(rec.assertion, f"{sid} 須宣告核准結果的內容檢查")
        for fields in rec.asset_fields:
            require(fields and fields[0] in outputs, "素材欄位未對應輸出")
        for fields in rec.text_inputs.values():
            require(fields and fields[0] in inputs, "文字素材未對應輸入")
        for program in (rec.arguments, rec.result, rec.assertion):
            if program:
                validate_program(program)
        if rec.arguments or rec.result:
            require(len(rec.examples) >= 2, f"{sid} 候選程式至少需要兩組測例")
            require(
                len({json.dumps(e.context, sort_keys=True) for e in rec.examples}) >= 2,
                "測例輸入重複",
            )
            for ex in rec.examples:
                if rec.arguments and rec.result:
                    require(
                        ex.expected_arguments is not None
                        and transform(rec.arguments, ex.context)
                        == ex.expected_arguments,
                        f"{sid} 的輸入映射試做不符",
                    )
                actual = (
                    transform(rec.result, ex.context)
                    if rec.result
                    else transform(rec.arguments, ex.context)
                )
                require(actual == ex.expected, f"{sid} 的合成測例不符")
                if rec.result:
                    require(set(actual) == set(outputs), "候選輸出名稱不符")
                    for name, value in actual.items():
                        validate_value(contracts, name, value)
        node = rec.model_dump(exclude={"examples"})
        if rec.operation:
            ep = operations[rec.operation]
            node["operation_version"] = ep.dist.version
        steps.append(
            {
                **node,
                "id": sid,
                "title": s["title"],
                "blueprint_step": s,
                "inputs": inputs,
                "outputs": outputs,
                "review": after.get(sid),
                "skills": source["skills"]["steps"][sid],
            }
        )
        available.update(outputs)
        if sid == r.terminal_step:
            break
    require(not variant or steps[-1]["id"] == r.terminal_step, "替代流程終點不存在")
    require(set(r.steps) == {s["id"] for s in steps}, "綁定包含未執行步驟")
    finals = (
        r.final_outputs
        if variant
        else [x["name"] for x in source["definition"]["final_outputs"]["items"]]
    )
    require(finals and set(finals) <= available, "最終產物無法取得")
    return {
        "format": 1,
        "workflow": source["manifest"]["id"],
        "version": version,
        "source_hash": source["source_hash"],
        "inputs": source["inputs"],
        "contracts": contracts,
        "steps": steps,
        "final_outputs": finals,
        "decisions": r.decisions,
    }

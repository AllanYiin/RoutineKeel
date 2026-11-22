"""Read a blueprint as data. The ZIP is never extracted into an importable directory."""

import hashlib
import io
import re
import zipfile

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from ..package_updates import MAX_ZIP, _component, _members
from ..runtime import FlowError
from .program import load_json

REQUIRED = (
    "flow.json",
    "workflow.definition.json",
    "contracts.json",
    "dependencies.json",
    "skill-map.json",
    "gaps.json",
    "task-config.example.json",
    "glossary.json",
)
IDENT = re.compile(r"[a-z][a-z0-9_]{0,62}")


def require(condition, message):
    if not condition:
        raise FlowError("INVALID_CONFIGURATION", message)


def indexed(rows, field="id"):
    require(isinstance(rows, list), "項目清單格式錯誤")
    result = {}
    for row in rows:
        key = row[field]
        require(
            isinstance(key, str) and key.strip() and key not in result,
            "識別碼空白或重複",
        )
        result[key] = row
    return result


def review_roles(points):
    return ({p["after_step"]: p for p in points if not p.get("is_step")},
            {p["is_step"] for p in points if p.get("is_step")})


def check_schema(schema):
    def walk(value):
        if isinstance(value, dict):
            require(
                not {"$ref", "$dynamicRef", "$id"} & value.keys()
                and value.get("$schema", "https://json-schema.org/draft/2020-12/schema")
                == "https://json-schema.org/draft/2020-12/schema",
                "請使用內嵌的 Draft 2020-12 合約",
            )
            if isinstance(value.get("format"), str):
                require(
                    value["format"] in FormatChecker().checkers,
                    "此環境缺少指定格式的檢查器",
                )
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    Draft202012Validator.check_schema(schema)


def validate_value(contracts, name, value):
    if name in contracts:
        error = next(
            Draft202012Validator(
                contracts[name]["schema"], format_checker=FormatChecker()
            ).iter_errors(value),
            None,
        )
        require(
            error is None,
            f"產物 {name} 不符合合約" + (f" ({error.json_path})" if error else ""),
        )


def inspect_blueprint(raw):
    require(0 < len(raw) <= MAX_ZIP, "藍圖 ZIP 超過大小限制")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = {}
            roots = set()
            for info, parts in _members(archive, limit=32 * 1024**2, count=2048):
                roots.add(parts[0])
                if not info.is_dir():
                    require(len(parts) >= 2, "藍圖需要單一外層目錄")
                    files["/".join(parts[1:])] = archive.read(info)
            require(len(roots) == 1, "藍圖需要單一外層目錄")
            if "blueprint.json" in files:
                from ..graph.source import graph_source

                require(files.keys() == {"blueprint.json"}, "Graph v2 包含未宣告成員")
                payload = load_json(files["blueprint.json"].decode("utf-8-sig"))
                return graph_source(payload, raw), files
            require(
                all(n in files for n in REQUIRED),
                "藍圖缺少宣告或含多個根目錄",
            )
        docs = {name: load_json(files[name].decode("utf-8-sig")) for name in REQUIRED}
        (
            manifest,
            definition,
            contracts,
            dependencies,
            mapping,
            gaps,
            config,
            glossary,
        ) = docs.values()
        require(
            manifest["runtime_api"] == "0.2"
            and manifest["schema_version"] == 1
            and definition["definition_version"] == "0.2"
            and definition["execution"]["order"] == "linear",
            "不支援的藍圖或執行順序",
        )
        ident = manifest["id"]
        _component(ident)
        require(
            IDENT.fullmatch(ident)
            and ident not in {"flow_base", "share_states", "share_logs"}
            and ident == definition["workflow_id"]
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest["version"]),
            "藍圖識別或版本不符",
        )
        steps = indexed(definition["steps"])
        require(1 <= len(steps) <= 200, "藍圖須有 1 到 200 個步驟")
        require(
            definition.get("skill_map", "skill-map.json") == "skill-map.json"
            and definition.get("gaps", "gaps.json") == "gaps.json",
            "步驟圖引用與包內宣告不符",
        )
        skills = indexed(mapping["skills"])
        for name in files:
            if name in (*REQUIRED, "AGENTS.md", "README.md"):
                continue
            parts = name.split("/")
            require(
                len(parts) >= 3
                and parts[0] == "skills"
                and parts[1] in skills
                and not any(
                    p.startswith(".")
                    or p in {"outputs", "settings.json", "__pycache__"}
                    for p in parts
                )
                and parts[-1]
                .lower()
                .endswith(
                    (
                        ".json",
                        ".md",
                        ".txt",
                        ".py",
                        ".yaml",
                        ".yml",
                        ".ttf",
                        ".otf",
                        ".toml",
                        ".sh",
                        ".ps1",
                        ".csv",
                        ".tsv",
                    )
                ),
                "來源包包含未宣告或私人檔案",
            )
        programs = indexed(gaps["deterministic_code"] + gaps["mcp_calls"])
        deps = indexed(dependencies["items"])
        require(
            set(steps) == set(contracts["stages"]) == set(mapping["steps"]),
            "步驟對應不完整",
        )
        for skill_id, skill in skills.items():
            require(
                skill["folder"] == "skills/" + skill_id
                and "/" not in skill_id
                and skill["folder"] + "/SKILL.md" in files,
                "技能來源缺失",
            )
            require(
                set(skill.get("uses", [])) <= skills.keys()
                and set(skill.get("gaps", [])) <= programs.keys(),
                "技能引用的能力或缺口不存在",
            )
            require(
                set(skill["used_by"])
                == {s for s, ids in mapping["steps"].items() if skill_id in ids},
                "技能正反向步驟不符",
            )
        from graphlib import TopologicalSorter

        tuple(
            TopologicalSorter(
                {k: v.get("uses", []) for k, v in skills.items()}
            ).static_order()
        )
        for row in [*deps.values(), *programs.values()]:
            require(set(row["used_by"]) <= steps.keys(), "依賴或缺口引用未知步驟")
        artifacts = contracts["artifacts"]
        for item in artifacts.values():
            check_schema(item["schema"])
        producers = {}
        required_inputs = []
        for sid, step in steps.items():
            stage = contracts["stages"][sid]
            require(
                step["inputs"] == stage["inputs"]
                and step["outputs"] == stage["outputs"]
                and set(mapping["steps"][sid]) <= skills.keys(),
                f"{sid} 的步驟、合約或技能不符",
            )
            for port in step["inputs"]:
                if port not in producers and port not in required_inputs:
                    required_inputs.append(port)
            for port in step["outputs"]:
                require(
                    port in artifacts
                    and port not in producers
                    and port not in required_inputs,
                    f"{sid} 輸出未宣告、重複或被提前讀取",
                )
                producers[port] = sid
        for terminal in definition["final_outputs"]["items"]:
            require(
                producers.get(terminal["name"]) == terminal["from"], "最終產物來源不符"
            )
        for point in definition["review"]["points"]:
            require(
                point["after_step"] in steps
                and point.get("is_step", point["after_step"]) in steps,
                "人審引用未知步驟",
            )
        findings = []
        for item in definition.get("inputs_per_run", {}).get("items", []):
            name = item["name"]
            actual = [sid for sid, s in steps.items() if name in s["inputs"]]
            if set(item.get("used_by", actual)) != set(actual):
                findings.append(
                    {
                        "kind": "input_consumers",
                        "name": name,
                        "summary": item.get("used_by"),
                        "stages": actual,
                        "source": "workflow.definition.json#/inputs_per_run",
                    }
                )
        return {
            "source_hash": hashlib.sha256(raw).hexdigest(),
            "manifest": manifest,
            "definition": definition,
            "contracts": artifacts,
            "steps": list(steps),
            "inputs": required_inputs,
            "skills": mapping,
            "dependencies": deps,
            "gaps": programs,
            "configuration": config,
            "glossary": glossary,
            "findings": findings,
        }, files
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        RecursionError,
        SchemaError,
        zipfile.BadZipFile,
    ) as exc:
        raise FlowError("INVALID_CONFIGURATION", "藍圖 ZIP 或宣告格式無效") from exc

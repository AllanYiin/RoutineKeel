"""Load verified v1 skill attachments through a disposable Documa catalog."""

import re
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from routinekeel_contracts.documa import call_documa, documa_cli, load_skill_bundle
from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.paths import safe_relative

from ..runtime import FlowError


def _skill_text(source, name, alias):
    text = source.decode("utf-8-sig")
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
    metadata = yaml.safe_load(match[1]) if match else {}
    if not isinstance(metadata, dict):
        raise TypeError("invalid skill frontmatter")
    metadata.update(name=alias, description=metadata.get("description") or name)
    body = text[match.end() :] if match else text
    return "---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + body


def _materialize(catalog, aliases, declared, files):
    for name, payload in files.items():
        if not name.startswith("skills/"):
            continue
        _, key, relative = name.split("/", 2)
        relative = safe_relative(relative)
        if relative == "skill_lifecycle.yaml":
            continue
        target = catalog / aliases[key] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative == "SKILL.md":
            target.write_text(_skill_text(payload, key, aliases[key]), encoding="utf-8")
        else:
            target.write_bytes(payload)
    for key, alias in aliases.items():
        dependencies = [
            f"bundle:{aliases[child]}" for child in declared[key].get("uses", [])
        ]
        (catalog / alias / "skill_lifecycle.yaml").write_text(
            yaml.safe_dump({"dependencies": {"skills": dependencies}}), encoding="utf-8"
        )


def load_bundled_skills(workspace, plan, source, files):
    selected = [node for node in plan["steps"] if node["skills"]]
    for node in plan["steps"]:
        node["skill_text"] = {}
    if not selected:
        return
    declared = {row["id"]: row for row in source["skills"]["skills"]}
    aliases = {key: f"skill-{number}" for number, key in enumerate(sorted(declared), 1)}
    try:
        executable = documa_cli(workspace)
        with TemporaryDirectory(prefix="rk-bundled-skills-") as temporary:
            directory = Path(temporary)
            catalog, store = directory / "catalog", directory / "store"
            _materialize(catalog, aliases, declared, files)
            call_documa(
                executable,
                "skills",
                "root-add",
                "bundle",
                catalog,
                "--store-dir",
                store,
                directory=directory,
            )
            call_documa(
                executable, "skills", "sync", "--store-dir", store, directory=directory
            )
            for node in selected:
                referenced = list(node["skills"])
                for key in referenced:
                    referenced.extend(
                        child
                        for child in declared[key].get("uses", [])
                        if child not in referenced
                    )
                for key in referenced:
                    bundle = load_skill_bundle(
                        executable, store, node["title"], [f"bundle:{aliases[key]}"]
                    )
                    if bundle.get("status") != "ok" or not bundle.get(
                        "rendered_skill_md"
                    ):
                        raise FlowError(
                            "DOCUMA_SKILL_LOAD",
                            "Documa 無法載入藍圖內附技能。",
                            skill=key,
                            reason=bundle.get("code"),
                        )
                    node["skill_text"][key] = bundle["rendered_skill_md"]
    except (StudioError, OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise FlowError(
            "DOCUMA_SKILL_LOAD", "Documa 無法索引或載入藍圖內附技能。"
        ) from exc

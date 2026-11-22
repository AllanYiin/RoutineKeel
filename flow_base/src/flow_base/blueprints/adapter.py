"""Trusted adapter shared by installed blueprint wheels; wheel data is never executable source."""

from ..contracts import Workflow
from ..engine import Adapter as BaseAdapter
from ..runtime import atomic_json, atomic_copy, digest, read_json
from .source import require
from .sessions import Session, Assets, refs
from .recipe import local_operations
from ..outputs import reject_links
import shutil

ADAPTER_SOURCE = b"""from pathlib import Path
from flow_base.blueprints.adapter import BlueprintAdapter

class Adapter(BlueprintAdapter):
    package = Path(__file__).parent
"""


class BlueprintAdapter(BaseAdapter):
    package = None

    def __init__(self, package=None):
        if package is not None:
            self.package = package
        self.plan_data = read_json(self.package / "plan.json")
        self.handlers = {"blueprint.advance": self.run_step}

    def configuration(self, overrides):
        require(not overrides, "請在本次輸入的 config 提供流程設定")
        return Workflow.model_validate(
            {
                "id": self.plan_data["workflow"],
                "steps": [
                    {
                        "id": "advance",
                        "handler": "blueprint.advance",
                        "cacheable": False,
                        "side_effects": "local_write",
                    }
                ],
            }
        )

    def binding_path(self, runtime):
        path = runtime.home / "blueprint_bindings" / (digest(self.plan_data) + ".json")
        reject_links(path)
        return path

    def verify(self, runtime, workflow):
        installed = local_operations()
        missing = {
            n["operation"]
            for n in self.plan_data["steps"]
            if n["operation"]
            and (
                n["operation"] not in installed
                or installed[n["operation"]].dist.version != n["operation_version"]
            )
        }
        path = self.binding_path(runtime)
        if not path.is_file():
            missing.add("host_bindings")
        else:
            for b in read_json(path).values():
                missing.update(
                    cmd for cmd in b.get("executables", []) if shutil.which(cmd) is None
                )
        return {"ok": not missing, "profile": "core", "missing": sorted(missing)}

    def plan(self, runtime, workflow, source=None, output=None):
        result = super().plan(runtime, workflow, source, output)
        preflight = self.verify(runtime, workflow)
        if not preflight["ok"]:
            result["blocked"].append(
                {"code": "DEPENDENCY_UNAVAILABLE", "missing": preflight["missing"]}
            )
        return result

    def run_step(self, ctx, step, dependencies, target):
        require(ctx.source.stat().st_size <= 1024**2, "工作輸入描述超過 1 MiB")
        request = read_json(ctx.source)
        plan = self.plan_data
        if "session_id" in request:
            session = Session(ctx.runtime, request["session_id"])
            result = session.interact("next", {}, workflow=plan["workflow"])
        else:
            bindings_path = self.binding_path(ctx.runtime)
            require(bindings_path.is_file(), "請先在此工作區完成工具綁定與驗證")
            bindings = read_json(bindings_path)
            result = Session.begin(
                ctx.runtime,
                plan,
                request,
                ctx.source.parent,
                bindings,
                key=ctx.run_id.removeprefix("run_"),
            )
        self.assets = Assets(ctx.runtime)
        atomic_json(target, result)
        return target

    def publish_outputs(self, terminal, output, workflow):
        response = read_json(terminal)
        outputs = response.get("outputs")
        if outputs is not None:
            folder = output / "artifacts"
            atomic_json(folder / "results.json", outputs)
            for token in set(refs(outputs)):
                asset = self.assets.resolve(token)
                atomic_copy(asset, folder / asset.name)
        return {
            "status": "complete" if outputs is not None else "review_required",
            "blueprint": response,
        }

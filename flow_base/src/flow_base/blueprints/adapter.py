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
        if self.plan_data["format"]==2:
            from ..graph.capabilities import resolve_graph
            live=resolve_graph(self.plan_data["graph"],runtime.workspace_root)
            path=self.binding_path(runtime)
            ok=(path.is_file() and digest(read_json(path))==self.plan_data["bindings_hash"] and
                live["status"]=="ready" and live["fingerprint"]==self.plan_data["capability_fingerprint"])
            return {"ok":ok,"profile":"core","missing":[] if ok else ["graph_bindings"]}
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
        session_type=Session
        if plan["format"]==2:
            from ..graph.session import GraphSession
            session_type=GraphSession
        if "session_id" in request:
            session = session_type(ctx.runtime, request["session_id"])
            require(digest(plan)==digest(read_json(session.path)["plan"]),"工作執行版本已變更")
            result = session.interact("next", {}, workflow=plan["workflow"])
        else:
            bindings_path = self.binding_path(ctx.runtime)
            require(bindings_path.is_file(), "請先在此工作區完成工具綁定與驗證")
            bindings = read_json(bindings_path)
            result = session_type.begin(
                ctx.runtime,
                plan,
                request,
                ctx.source.parent,
                bindings,
                key=ctx.run_id.removeprefix("run_"),
                **({"offline":ctx.offline} if plan["format"]==2 else {}),
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
        status = response["status"] if response["status"] in {"failed","cancelled"} else (
            "complete" if outputs is not None else "review_required"
        )
        report = {"status":status,"blueprint":response}
        if self.plan_data["format"]==2:
            business = response.get("result_status","not_evaluated")
            report.update(status=business if outputs is not None else status, graph_result_status=business,
                          graph_delivery_status="eligible" if business=="accepted" else "withheld")
        return report

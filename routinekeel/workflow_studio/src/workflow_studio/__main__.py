import argparse
import json
from pathlib import Path

from .service import StudioService


def main(argv=None):
    parser = argparse.ArgumentParser(description="RoutineKeel 工作流程製作工作台")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--runtime-python", type=Path)
    parser.add_argument(
        "command", choices=("ui", "mcp", "projects", "status"), nargs="?", default="ui"
    )
    args = parser.parse_args(argv)
    if args.command == "mcp":
        from .guided.mcp_server import run_stdio

        run_stdio(args.workspace)
    elif args.command == "ui":
        from .ui.app import StudioApp
        from .ui.model import StudioUIModel

        StudioApp(
            args.workspace, model=StudioUIModel(args.workspace, runtime_python=args.runtime_python)
        ).run()
    else:
        with StudioService.open_workspace(args.workspace) as studio:
            result = (
                studio.status()
                if args.command == "status"
                else [
                    {
                        "project_id": p.project_id,
                        "name": p.name,
                        "version": p.version,
                        "status": p.status,
                    }
                    for p in studio.projects.list(include_archived=True)
                ]
            )
            print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

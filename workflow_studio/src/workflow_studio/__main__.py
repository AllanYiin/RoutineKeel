import argparse
import json
from pathlib import Path

from .service import StudioService


def main(argv=None):
    parser = argparse.ArgumentParser(description="RoutineKeel 對話內工作流程設計")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--runtime-python", type=Path)
    parser.add_argument(
        "command", choices=("guide", "projects", "status"), nargs="?", default="guide"
    )
    args = parser.parse_args(argv)
    if args.command == "guide":
        from .conversation.guide import guide

        print(json.dumps({"ok": True, "result": guide()}, ensure_ascii=False))
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

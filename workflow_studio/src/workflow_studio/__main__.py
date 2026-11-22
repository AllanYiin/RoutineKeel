import argparse
import json
import sys
from pathlib import Path

from .service import StudioService


def main(argv=None):
    parser = argparse.ArgumentParser(description="RoutineKeel 對話內工作流程設計")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--runtime-python", type=Path)
    parser.add_argument(
        "command", choices=("guide", "projects", "status", "interview"), nargs="?", default="guide"
    )
    parser.add_argument("operation", nargs="?")
    parser.add_argument("--payload", default=None, help="JSON 請求，或使用 - 從 stdin 讀取")
    args = parser.parse_args(argv)
    if args.command == "guide":
        from .conversation.guide import guide

        print(json.dumps({"ok": True, "result": guide()}, ensure_ascii=False))
    elif args.command == "interview":
        from .contracts import StudioError
        from .conversation.facade import dispatch, stream_text

        try:
            if args.operation == "stream":
                deltas = (json.loads(line)["text"] for line in sys.stdin)
                for event in stream_text(deltas):
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                return 0
            raw = sys.stdin.read(1048577) if args.payload == "-" else args.payload or "{}"
            if len(raw) > 1048576:
                raise ValueError("too large")
            params = json.loads(raw)
            if not isinstance(params, dict):
                raise ValueError("object required")
            with StudioService.open_workspace(args.workspace) as studio:
                result = dispatch(studio, args.workspace, args.operation, params)
            response = {"ok": True, "result": result}
        except StudioError as exc:
            response = exc.as_response()
        except (ValueError, TypeError, KeyError, OSError):
            response = {
                "ok": False,
                "error": {"code": "INPUT_INVALID", "message": "請核對操作參數及附件路徑。"},
            }
        print(json.dumps(response, ensure_ascii=False))
        return 0 if response["ok"] else 2
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

"""Loopback page for the person's actions.

MCP tool visibility is not a security boundary, so confirming cards, consenting to source
disclosure and committing are reachable only here: a one-time ticket becomes an HttpOnly
session cookie, and every write also needs the same Origin and a CSRF token. The listener
lives only as long as the process that created it and never binds beyond 127.0.0.1.
"""

import base64
import json
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from ..contracts import StudioError
from ..ingestion.security import safe_relative
from ..service import StudioService
from .agent_tools import host_policy

MAX_BODY = 64 * 1024 * 1024
PREVIEW_CHARS = 2000
COOKIE = "rk_studio_session"


def _case_suggestions(sources, existing):
    """Pair files that sit under input/ and output/ with the same remaining path."""
    pairs = []
    for source in sources:
        paths = {f["path"] for f in source["files"]}
        for path in sorted(paths):
            head, _, rest = path.partition("/")
            expected = "output/" + rest
            if head == "input" and expected in paths and f"{path} → {expected}" not in existing:
                pairs.append(
                    {
                        "source_id": source["source_id"],
                        "input_path": path,
                        "expected_path": expected,
                    }
                )
    return pairs


class LocalPage:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.tickets, self.sessions = {}, {}
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def url(self, project_id):
        ticket = secrets.token_urlsafe(32)
        with self.lock:
            self.tickets[ticket] = project_id
        # Fragment, not query: it is never sent in a request line or logged by a proxy.
        return f"{self.origin}/#ticket={ticket}"

    def _redeem(self, ticket):
        with self.lock:
            project_id = self.tickets.pop(ticket, None)
            if project_id is None:
                raise StudioError("APPROVAL_REQUIRED", "連結已使用或已失效，請回到對話重新開啟。")
            session = secrets.token_urlsafe(32)
            self.sessions[session] = {"project_id": project_id, "csrf": secrets.token_urlsafe(32)}
        return session

    def _studio(self):
        studio = StudioService.open_workspace(self.workspace)
        studio.tools.seed()
        return studio

    def view(self, project_id):
        with self._studio() as studio:
            project = studio.projects.get(project_id)
            state = studio.guided(host_policy()).state(project_id)
            sources = []
            for source in studio.sources.list(project_id):
                files = []
                for record in source.manifest["files"]:
                    content = studio.sources.content(project_id, source.source_id, record["path"])
                    try:
                        preview = content[: PREVIEW_CHARS * 4].decode("utf-8-sig")[:PREVIEW_CHARS]
                    except UnicodeDecodeError:
                        preview = None
                    files.append(
                        {"path": record["path"], "size": record["size"], "preview": preview}
                    )
                sources.append({"source_id": source.source_id, "name": source.name, "files": files})
        state.pop("draft")
        state.pop("answers")
        return {
            "project": {"project_id": project_id, "name": project.name},
            "sources": sources,
            "case_suggestions": _case_suggestions(sources, {c["name"] for c in state["cases"]}),
            "publish_target": str(self.workspace.resolve()),
            **state,
        }

    def act(self, project_id, action, body):
        with self._studio() as studio:
            guided = studio.guided(host_policy())
            if action == "import":
                version = studio.projects.get(project_id).version
                if isinstance(body.get("text"), str) and body["text"].strip():
                    studio.sources.import_text(project_id, version, body["text"])
                else:
                    self._import_folder(studio, project_id, version, body.get("folder"))
            elif action == "consent":
                guided.consent(project_id, body.get("version"), body.get("source_ids"))
            elif action == "answer":
                guided.answer(
                    project_id,
                    body.get("version"),
                    body.get("card_id"),
                    body.get("action"),
                    body.get("value"),
                )
            elif action == "reopen":
                guided.reopen(project_id, body.get("version"))
            elif action == "commit":
                guided.commit(project_id, body.get("version"), actor=body.get("actor"))
            elif action == "add_case":
                guided.add_case(
                    project_id,
                    body.get("version"),
                    body.get("source_id"),
                    body.get("input_path"),
                    body.get("expected_path"),
                )
            elif action == "publish":
                # The target is fixed to this workspace; a page request cannot redirect it.
                guided.publish(
                    project_id,
                    body.get("version"),
                    actor=body.get("actor"),
                    target_workspace=self.workspace,
                )
            else:
                raise StudioError("INPUT_INVALID", "不支援的操作。")
        return self.view(project_id)

    @staticmethod
    def _import_folder(studio, project_id, version, folder):
        files = folder.get("files") if isinstance(folder, dict) else None
        if not isinstance(files, list) or not files or not isinstance(folder.get("name"), str):
            raise StudioError("INPUT_INVALID", "請貼上文字，或選擇含有檔案的資料夾。")
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / (Path(folder["name"]).name or "案例")
            for item in files:
                relative = item.get("path") if isinstance(item, dict) else None
                if not isinstance(relative, str) or not isinstance(item.get("data"), str):
                    raise StudioError("INPUT_INVALID", "資料夾內容格式不正確。")
                safe_relative(relative)
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(item["data"], validate=True))
            studio.sources.import_path(project_id, version, root)

    def _handler(page):  # noqa: N805 - closes over the page instead of a module global
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # stdout belongs to the MCP stdio transport.

            def _send(self, status, payload, *, cookie=None, content_type="application/json"):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
                )
                if cookie:
                    self.send_header(
                        "Set-Cookie", f"{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/"
                    )
                self.end_headers()
                self.wfile.write(data)

            def _session(self):
                for part in self.headers.get("Cookie", "").split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == COOKIE and value in page.sessions:
                        return page.sessions[value]
                raise StudioError("APPROVAL_REQUIRED", "請從對話中的連結開啟這個頁面。")

            def _guard(self, write):
                # A DNS-rebinding page would arrive with its own Host; a cross-site form with its own Origin.
                if self.headers.get("Host") != page.origin.removeprefix("http://"):
                    raise StudioError("APPROVAL_REQUIRED", "來源位址不被接受。")
                if write and self.headers.get("Origin") != page.origin:
                    raise StudioError("APPROVAL_REQUIRED", "來源網站不被接受。")

            def _respond(self, call):
                try:
                    self._send(200, {"ok": True, "result": call()})
                except StudioError as exc:
                    self._send(403 if exc.code == "APPROVAL_REQUIRED" else 400, exc.as_response())
                except (ValueError, TypeError, KeyError):
                    self._send(
                        400,
                        {
                            "ok": False,
                            "error": {"code": "INPUT_INVALID", "message": "請求格式不正確。"},
                        },
                    )

            def do_GET(self):  # noqa: N802
                if self.path == "/":
                    page_html = (
                        resources.files("workflow_studio.guided")
                        .joinpath("cards.html")
                        .read_bytes()
                    )
                    return self._send(200, page_html, content_type="text/html")

                def view():
                    self._guard(False)
                    if self.path != "/api/view":
                        raise StudioError("INPUT_INVALID", "找不到此位置。")
                    session = self._session()
                    return {"csrf": session["csrf"], "view": page.view(session["project_id"])}

                self._respond(view)

            def do_POST(self):  # noqa: N802
                def write():
                    self._guard(True)
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_BODY:
                        raise StudioError("INPUT_INVALID", "內容大小超過限制。")
                    body = json.loads(self.rfile.read(length))
                    if self.path == "/api/session":
                        self._cookie = page._redeem(body.get("ticket"))
                        return {}
                    session = self._session()
                    if not secrets.compare_digest(
                        self.headers.get("X-RK-CSRF", ""), session["csrf"]
                    ):
                        raise StudioError("APPROVAL_REQUIRED", "頁面已過期，請重新整理。")
                    return page.act(session["project_id"], self.path.removeprefix("/api/"), body)

                self._cookie = None
                try:
                    result = write()
                except StudioError as exc:
                    return self._send(
                        403 if exc.code == "APPROVAL_REQUIRED" else 400, exc.as_response()
                    )
                except (ValueError, TypeError, KeyError):
                    return self._send(
                        400,
                        {
                            "ok": False,
                            "error": {"code": "INPUT_INVALID", "message": "請求格式不正確。"},
                        },
                    )
                self._send(200, {"ok": True, "result": result}, cookie=self._cookie)

        return Handler

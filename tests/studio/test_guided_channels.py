import base64
import json
import urllib.error
import urllib.request

import pytest
from test_guided_session import sketch
from workflow_studio.contracts import StudioError
from workflow_studio.guided.agent_tools import AgentTools
from workflow_studio.guided.local_page import COOKIE, LocalPage

pytest.importorskip("PIL", reason="Guided channel fixtures require Studio source dependencies")


class Browser:
    """Minimal same-origin client: keeps the cookie and CSRF token like the page script does."""

    def __init__(self, page, url=None):
        self.origin, self.cookie, self.csrf = page.origin, None, ""
        if url:
            self.post("/api/session", {"ticket": url.split("#ticket=")[1]})
            self.view()

    def post(self, path, body=None, headers=None):
        base = {"Origin": self.origin, "X-RK-CSRF": self.csrf, "Content-Type": "application/json"}
        if self.cookie:
            base["Cookie"] = f"{COOKIE}={self.cookie}"
        base.update(headers or {})
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.origin + path, data=data, headers=base)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                cookie = response.headers.get("Set-Cookie")
                if cookie:
                    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
                    self.cookie = cookie.split(";")[0].split("=", 1)[1]
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def view(self):
        status, data = self.post("/api/view")
        assert status == 200, data
        self.csrf = data["result"]["csrf"]
        return data["result"]["view"]


@pytest.fixture
def channels(tmp_path):
    page = LocalPage(tmp_path / "studio")
    agent = AgentTools(tmp_path / "studio", page.url)
    yield page, agent
    page.close()


def test_agent_and_person_complete_a_flow_through_their_own_channels(channels):
    page, agent = channels
    opened = agent.open(name="複製流程")
    project_id = opened["project_id"]
    person = Browser(page, opened["page_url"])
    status, data = person.post("/api/import", {"text": "把輸入檔案完整複製成成果\n"})
    assert status == 200, data
    source = agent.sources(project_id)[0]
    assert source["readable"] is False
    with pytest.raises(StudioError) as caught:
        agent.source_read(project_id, source["source_id"], "說明.txt")
    assert caught.value.code == "APPROVAL_REQUIRED"
    view = person.view()
    person.post("/api/consent", {"version": view["version"], "source_ids": [source["source_id"]]})
    read = agent.source_read(project_id, source["source_id"], "說明.txt")
    assert read["text"].startswith("把輸入檔案") and read["next_offset"] is None

    class Ref:
        source_id, version = source["source_id"], source["version"]

    state = agent.state(project_id)
    state = agent.submit_draft(project_id, state["version"], {"ir": sketch(Ref)})
    assert state["next"] == {"actor": "person", "action": "answer"}
    view = person.view()
    assert "draft" not in view and view["cards"][0]["card_id"] == "goal"
    status, data = person.post(
        "/api/answer", {"version": view["version"], "card_id": "goal", "action": "confirm"}
    )
    assert status == 200, data
    status, data = person.post(
        "/api/commit", {"version": data["result"]["version"], "actor": "作者"}
    )
    assert status == 200 and data["result"]["stage"] == "committed", data
    assert agent.state(project_id)["next"] == {"actor": "person", "action": "add_case"}
    assert any(t["tool"] == "file.copy" and t["compilable"] for t in agent.tools())


def test_agent_surface_has_no_person_actions():
    public = {name for name in vars(AgentTools) if not name.startswith("_")}
    assert public == {
        "open",
        "state",
        "tools",
        "sources",
        "source_read",
        "submit_draft",
        "respond_reflection",
        "trial",
    }


def test_page_refuses_reused_ticket_foreign_origin_and_missing_csrf(channels):
    page, agent = channels
    opened = agent.open(name="防護")
    person = Browser(page, opened["page_url"])
    view = person.view()
    assert person.post("/api/consent", {"version": view["version"], "source_ids": []})[0] == 200
    body = {"version": view["version"] + 1, "source_ids": []}
    assert person.post("/api/consent", body, {"Origin": "http://evil.example"})[0] == 403
    assert person.post("/api/consent", body, {"X-RK-CSRF": "wrong"})[0] == 403
    assert person.post("/api/consent", body, {"Host": "evil.example"})[0] == 403
    stranger = Browser(page)
    assert stranger.post("/api/view")[0] == 403
    ticket = opened["page_url"].split("#ticket=")[1]
    assert stranger.post("/api/session", {"ticket": ticket})[0] == 403
    assert stranger.post("/api/commit", {"version": 1, "actor": "x"})[0] == 403


def test_folder_upload_keeps_structure_and_rejects_escaping_paths(channels):
    page, agent = channels
    opened = agent.open(name="資料夾")
    person = Browser(page, opened["page_url"])

    def encode(text):
        return base64.b64encode(text.encode()).decode()

    folder = {
        "name": "案例",
        "files": [
            {"path": "input/a.txt", "data": encode("same\n")},
            {"path": "output/a.txt", "data": encode("same\n")},
        ],
    }
    status, data = person.post("/api/import", {"folder": folder})
    assert status == 200, data
    files = [f["path"] for f in data["result"]["sources"][0]["files"]]
    assert files == ["input/a.txt", "output/a.txt"]
    folder["files"][0]["path"] = "../escape.txt"
    assert person.post("/api/import", {"folder": folder})[0] == 400
    assert len(person.view()["sources"]) == 1

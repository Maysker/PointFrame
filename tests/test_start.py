import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from pointframe import cli
from pointframe.crop_server import ServerSession, handler_factory
from test_crop import write_cloud


def test_start_screen_cancel_then_open_cloud(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "selected.ply"
    write_cloud(source, [(0, 0, 0, 0, 0, 1, 1, 2, 3),
                         (1, 0, 0, 0, 0, 1, 4, 5, 6),
                         (0, 1, 0, 0, 0, 1, 7, 8, 9)])
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    choices = iter([None, source])
    session = ServerSession(None, None, 3, tmp_path / "exports", lambda: next(choices),
                            cli.default_workspace)
    assert session.app is None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(session))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        assert b"Open point cloud" in response.read()
        connection.request("GET", "/start.js")
        response = connection.getresponse()
        assert response.status == 200
        assert b"/api/open" in response.read()

        connection.request("POST", "/api/open", "{}", {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"opened": False}
        assert session.app is None
        connection.request("GET", "/")
        assert b"Open point cloud" in connection.getresponse().read()

        connection.request("POST", "/api/open", "{}", {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"opened": True}
        assert session.app.layout.path == source
        assert session.app.workspace == cli.default_workspace(source)
        assert session.app.output_dir == tmp_path / "exports"
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        assert b"Selection outline" in response.read()
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["phase"] in {"preparing", "ready"}
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_explicit_cloud_opens_viewer_directly(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    write_cloud(source, [(0, 0, 0, 0, 0, 1, 1, 2, 3),
                         (1, 0, 0, 0, 0, 1, 4, 5, 6),
                         (0, 1, 0, 0, 0, 1, 7, 8, 9)])
    session = ServerSession(source, tmp_path / "workspace", 3, tmp_path / "exports", None, None)
    assert session.app.layout.path == source

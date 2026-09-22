import sys
import threading
from types import SimpleNamespace

import pointframe.crop_server as crop_server


def test_pywebview_wraps_existing_localhost_url(monkeypatch) -> None:
    calls = []
    fake_webview = SimpleNamespace(
        create_window=lambda title, url: calls.append((title, url)),
        start=lambda: calls.append("closed"),
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    assert crop_server.open_desktop_window("http://127.0.0.1:8765") is True
    assert calls == [("PointFrame", "http://127.0.0.1:8765"), "closed"]


def test_missing_pywebview_uses_browser_fallback(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "webview", None)
    assert crop_server.open_desktop_window("http://127.0.0.1:8765") is False


def test_closing_window_shuts_down_local_server(monkeypatch) -> None:
    stopped = threading.Event()
    serving = threading.Event()
    calls = []

    class FakeServer:
        server_address = ("127.0.0.1", 8765)

        def __init__(self, address, handler):
            calls.append("created")

        def serve_forever(self):
            serving.set()
            stopped.wait(5)
            calls.append("serve_stopped")

        def shutdown(self):
            calls.append("shutdown")
            stopped.set()

        def server_close(self):
            calls.append("closed")

    monkeypatch.setattr(crop_server, "ThreadingHTTPServer", FakeServer)

    def fake_window(url):
        assert url == "http://127.0.0.1:8765"
        assert serving.wait(5)
        calls.append("window_closed")
        return True

    monkeypatch.setattr(crop_server, "open_desktop_window", fake_window)
    monkeypatch.setattr(crop_server.webbrowser, "open",
                        lambda url: calls.append("browser"))
    crop_server.run_crop_ui(None, None, source_picker=lambda: None,
                            workspace_for_source=lambda source: source)
    assert "browser" not in calls
    assert calls.index("window_closed") < calls.index("shutdown")
    assert calls.index("shutdown") < calls.index("serve_stopped") < calls.index("closed")


def test_failed_window_launch_opens_browser(monkeypatch) -> None:
    calls = []

    class FakeServer:
        server_address = ("127.0.0.1", 8765)

        def __init__(self, address, handler):
            pass

        def serve_forever(self):
            calls.append("served")

        def shutdown(self):
            calls.append("shutdown")

        def server_close(self):
            calls.append("closed")

    monkeypatch.setattr(crop_server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(crop_server, "open_desktop_window", lambda url: False)
    monkeypatch.setattr(crop_server.webbrowser, "open", lambda url: calls.append(url))
    crop_server.run_crop_ui(None, None, source_picker=lambda: None,
                            workspace_for_source=lambda source: source)
    assert "http://127.0.0.1:8765" in calls
    assert calls[-2:] == ["shutdown", "closed"]

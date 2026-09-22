from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .crop import export_inventory, export_versioned, load_definition, parse_binary_ply, prepare_preview, save_definition, validate_definition


STATIC_ROOT = Path(__file__).with_name("crop_web")


def pick_export_directory() -> Path | None:
    """Choose a local export folder with the Linux desktop dialog."""
    zenity = shutil.which("zenity")
    if zenity is None:
        raise RuntimeError("Choosing an export folder requires Zenity; install it or use --output-dir")
    result = subprocess.run(
        [zenity, "--file-selection", "--directory", "--title=Choose PointFrame export folder"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Export folder picker failed")
    selected = result.stdout.rstrip("\n")
    if not selected:
        return None
    directory = Path(selected).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Select an export folder")
    return directory


class CropApplication:
    def __init__(self, source: Path, workspace: Path, target_points: int,
                 output_dir: Path | None = None) -> None:
        self.layout = parse_binary_ply(source)
        self.workspace = workspace.expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.output_dir = output_dir.expanduser().resolve() if output_dir else None
        self.target_points = target_points
        self.lock = threading.Lock()
        self.destination_lock = threading.Lock()
        self.state: dict[str, Any] = {"phase": "preparing", "progress": 0.0, "message": "Starting preview preparation"}
        threading.Thread(target=self._prepare, daemon=True).start()

    def update(self, progress: float, message: str) -> None:
        with self.lock:
            self.state.update(progress=progress, message=message)

    def _prepare(self) -> None:
        try:
            metadata = prepare_preview(self.layout, self.workspace, self.target_points, self.update)
            with self.lock:
                self.state = {"phase": "ready", "progress": 1.0, "message": "Preview ready", "metadata": metadata}
        except Exception as error:
            with self.lock:
                self.state = {"phase": "error", "progress": 0.0, "message": str(error), "traceback": traceback.format_exc()}

    def choose_export_destination(self) -> dict[str, Any]:
        with self.destination_lock:
            if self.output_dir is None:
                self.output_dir = pick_export_directory()
            return {"selected": self.output_dir is not None,
                    "directory": str(self.output_dir) if self.output_dir else None}

    def start_export(self, definition: dict[str, Any], action: str) -> None:
        definition = validate_definition(definition)
        if action not in {"new", "replace_latest"}:
            raise ValueError("Export action must explicitly be 'new' or 'replace_latest'")
        with self.lock:
            if self.output_dir is None:
                raise RuntimeError("Choose an export folder before exporting")
            if self.state.get("phase") == "exporting":
                raise RuntimeError("An export is already running")
            metadata = self.state.get("metadata")
            if not metadata:
                raise RuntimeError("Wait for preview preparation to finish")
            self.state.pop("report", None)
            self.state.update(phase="exporting", progress=0.0, message="Starting exact full-resolution export")
            output_dir = self.output_dir
        def run() -> None:
            try:
                report = export_versioned(self.layout, output_dir, definition, action,
                                          metadata["source_sha256"], self.update)
                with self.lock:
                    self.state.update(phase="exported", progress=1.0, message="Export complete", report=report)
            except Exception as error:
                with self.lock:
                    self.state.update(phase="error", message=str(error), traceback=traceback.format_exc())
        threading.Thread(target=run, daemon=True).start()


class ServerSession:
    def __init__(self, source: Path | None, workspace: Path | None, target_points: int,
                 output_dir: Path | None, source_picker: Callable[[], Path | None] | None,
                 workspace_for_source: Callable[[Path], Path] | None) -> None:
        self.app = CropApplication(source, workspace, target_points, output_dir) if source and workspace else None
        self.target_points = target_points
        self.output_dir = output_dir
        self.source_picker = source_picker
        self.workspace_for_source = workspace_for_source
        self.open_lock = threading.Lock()

    def open_cloud(self) -> dict[str, bool]:
        with self.open_lock:
            if self.app is not None:
                return {"opened": True}
            if self.source_picker is None or self.workspace_for_source is None:
                raise RuntimeError("Point cloud picker is unavailable")
            selected = self.source_picker()
            if selected is None:
                return {"opened": False}
            source = selected.expanduser().resolve(strict=True)
            if source.suffix.lower() != ".ply":
                raise ValueError("Select a .ply file")
            self.app = CropApplication(source, self.workspace_for_source(source),
                                       self.target_points, self.output_dir)
            return {"opened": True}


def handler_factory(session: ServerSession) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            print(f"[crop-ui] {format % args}")

        def send_json(self, value: Any, status: int = 200) -> None:
            payload = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                page = STATIC_ROOT / ("index.html" if session.app else "start.html")
                self._file(page, "text/html; charset=utf-8")
                return
            app = session.app
            if app is None and path.startswith("/api/"):
                self.send_json({"error": "Open a point cloud first"}, 409)
                return
            if path == "/api/status":
                with app.lock:
                    self.send_json(dict(app.state))
                return
            if path == "/api/preview":
                preview = app.workspace / "preview.bin"
                if not preview.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._file(preview, "application/octet-stream")
                return
            if path == "/api/exports":
                self.send_json(export_inventory(app.output_dir) if app.output_dir else
                               {"versions": [], "latest": None, "legacy_export": False, "legacy_files": []})
                return
            if path == "/api/definition":
                definition = app.workspace / "crop_definition.json"
                if not definition.exists():
                    self.send_json({"exists": False}, 404)
                else:
                    self.send_json({"exists": True, "definition": load_definition(definition)})
                return
            relative = "start.html" if path == "/index.html" and app is None else path.lstrip("/")
            candidate = (STATIC_ROOT / relative).resolve()
            if STATIC_ROOT.resolve() not in candidate.parents or not candidate.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._file(candidate, mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")

        def _file(self, path: Path, content_type: str) -> None:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as stream:
                while data := stream.read(1024 * 1024):
                    self.wfile.write(data)

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == "/api/open":
                    self.send_json(session.open_cloud())
                    return
                app = session.app
                if app is None:
                    self.send_json({"error": "Open a point cloud first"}, 409)
                    return
                if path == "/api/definition":
                    save_definition(app.workspace / "crop_definition.json", data)
                    self.send_json({"saved": True})
                elif urlparse(self.path).path == "/api/export-destination":
                    self.send_json(app.choose_export_destination())
                elif urlparse(self.path).path == "/api/export":
                    if not isinstance(data, dict) or "definition" not in data or "action" not in data:
                        raise ValueError("Export request requires definition and explicit action")
                    app.start_export(data["definition"], data["action"])
                    self.send_json({"started": True}, 202)
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except FileExistsError as error:
                self.send_json({"error": str(error)}, 409)
            except (ValueError, RuntimeError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, 400)

    return Handler


def open_desktop_window(url: str) -> bool:
    """Show the existing localhost UI in PyWebView, if available."""
    try:
        import webview

        webview.create_window("PointFrame", url)
        webview.start()
    except Exception as error:
        print(f"PointFrame window unavailable ({error}); opening browser instead.")
        return False
    return True


def run_crop_ui(source: Path | None, workspace: Path | None, host: str = "127.0.0.1", port: int = 8765,
                target_points: int = 1_500_000, open_browser: bool = True,
                output_dir: Path | None = None,
                source_picker: Callable[[], Path | None] | None = None,
                workspace_for_source: Callable[[Path], Path] | None = None) -> None:
    session = ServerSession(source, workspace, target_points, output_dir,
                            source_picker, workspace_for_source)
    server = ThreadingHTTPServer((host, port), handler_factory(session))
    url = f"http://{host}:{server.server_address[1]}"
    print(f"Crop UI: {url}")
    if session.app:
        print(f"Source (read-only): {session.app.layout.path}")
        print(f"Workspace: {session.app.workspace}")
        print(f"Output directory: {session.app.output_dir or 'choose on first export'}")
    else:
        print("PointFrame start screen: choose a local PLY file in the browser")
    server_thread: threading.Thread | None = None
    try:
        if open_browser:
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            if not open_desktop_window(url):
                webbrowser.open(url)
                server_thread.join()
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        print("\nCrop UI stopped.")
    finally:
        if server_thread is not None:
            server.shutdown()
            server_thread.join()
        server.server_close()

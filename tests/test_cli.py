import subprocess
from pathlib import Path
from time import monotonic, sleep

import pytest

from pointframe import cli
import pointframe.crop_server as crop_server
from pointframe.crop_server import CropApplication

from test_crop import definition, write_cloud


def test_entry_point_uses_external_workspace_and_separate_output(tmp_path: Path, monkeypatch) -> None:
    source_dir = tmp_path / "clouds"
    source_dir.mkdir()
    source = source_dir / "cloud.ply"
    write_cloud(source, [(0, 0, 0, 0, 0, 1, 1, 2, 3),
                         (1, 0, 0, 0, 0, 1, 4, 5, 6),
                         (0, 1, 0, 0, 0, 1, 7, 8, 9)])
    data_home = tmp_path / "data"
    output = tmp_path / "chosen-exports"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    seen = {}

    def fake_run(source_arg, workspace, host, port, target_points, open_browser, output_dir):
        seen.update(source=source_arg, workspace=workspace, output_dir=output_dir,
                    host=host, port=port, target_points=target_points, open_browser=open_browser)

    monkeypatch.setattr(cli, "run_crop_ui", fake_run)
    monkeypatch.setattr(cli, "pick_ply_file", lambda: pytest.fail("Explicit path opened the picker"))
    assert cli.main([str(source), "--output-dir", str(output), "--no-open"]) == 0
    assert seen["source"] == source
    assert seen["workspace"].is_relative_to(data_home / "pointframe" / "workspaces")
    assert not seen["workspace"].is_relative_to(source_dir)
    assert seen["output_dir"] == output
    assert seen["open_browser"] is False
    assert cli.default_workspace(source) == seen["workspace"]


def test_no_input_uses_native_picker_and_existing_launch_flow(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "selected.ply"
    write_cloud(source, [(0, 0, 0, 0, 0, 1, 1, 2, 3),
                         (1, 0, 0, 0, 0, 1, 4, 5, 6),
                         (0, 1, 0, 0, 0, 1, 7, 8, 9)])
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/zenity")
    calls = []

    def fake_dialog(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, str(source) + "\n", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_dialog)
    monkeypatch.setattr(cli, "run_crop_ui", lambda *args: calls.append(args))
    assert cli.main(["--no-open"]) == 0
    command, options = calls[0]
    assert command == ["/usr/bin/zenity", "--file-selection", "--title=Select PLY point cloud",
                       "--file-filter=PLY files | *.ply *.PLY"]
    assert options["capture_output"] is True
    assert calls[1] == (source, cli.default_workspace(source), "127.0.0.1", 8765,
                        1_500_000, False, None)


def test_picker_cancel_exits_without_starting_server(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/zenity")
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 1, "", ""))
    monkeypatch.setattr(cli, "run_crop_ui", lambda *args: pytest.fail("Server started after cancel"))
    assert cli.main([]) == 0


def test_picker_rejects_non_ply_selection(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/zenity")
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, "/tmp/cloud.txt\n", ""))
    monkeypatch.setattr(cli, "run_crop_ui", lambda *args: pytest.fail("Server started for non-PLY"))
    with pytest.raises(SystemExit) as error:
        cli.main([])
    assert error.value.code == 2


def test_application_exports_to_output_dir(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "cloud.ply"
    write_cloud(source, [(0, 0, 0, 0, 0, 1, 1, 2, 3),
                         (1, 0, 0, 0, 0, 1, 4, 5, 6),
                         (0, 1, 0, 0, 0, 1, 7, 8, 9)])
    app = CropApplication(source, tmp_path / "workspace", 3, tmp_path / "output")
    monkeypatch.setattr(crop_server, "pick_export_directory",
                        lambda: pytest.fail("Explicit --output-dir opened the folder picker"))
    assert app.workspace == tmp_path / "workspace"
    assert app.output_dir == tmp_path / "output"
    assert app.choose_export_destination() == {"selected": True, "directory": str(app.output_dir)}
    assert app.layout.path == source
    deadline = monotonic() + 5
    while app.state["phase"] == "preparing" and monotonic() < deadline:
        sleep(0.01)
    assert app.state["phase"] == "ready"
    app.start_export(definition(), "new")
    while app.state["phase"] == "exporting" and monotonic() < deadline:
        sleep(0.01)
    assert app.state["phase"] == "exported"
    assert (app.output_dir / "crop_001" / "building_crop_raw.ply").is_file()
    assert (app.output_dir / "crop_001" / "building_crop_aligned.ply").is_file()
    assert not (app.workspace / "crop_001").exists()


def test_export_folder_cancel_then_choose_once(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    source = tmp_path / "clouds" / "scan.ply"
    source.parent.mkdir()
    write_cloud(source, [(0, 0, 0, 0, 0, 1, 1, 2, 3),
                         (1, 0, 0, 0, 0, 1, 4, 5, 6),
                         (0, 1, 0, 0, 0, 1, 7, 8, 9)])
    workspace = tmp_path / "data" / "pointframe" / "workspaces" / "scan"
    app = CropApplication(source, workspace, 3)
    assert app.workspace == workspace
    assert app.output_dir is None
    deadline = monotonic() + 5
    while app.state["phase"] == "preparing" and monotonic() < deadline:
        sleep(0.01)
    assert app.state["phase"] == "ready"
    calls = []
    chosen = home / "exports"
    chosen.mkdir()

    def fake_picker():
        calls.append(True)
        return None if len(calls) == 1 else chosen

    monkeypatch.setattr(crop_server, "pick_export_directory", fake_picker)
    assert app.choose_export_destination() == {"selected": False, "directory": None}
    with pytest.raises(RuntimeError, match="Choose an export folder"):
        app.start_export(definition(), "new")
    assert not (chosen / "crop_001").exists()
    assert app.state["phase"] == "ready"
    assert app.choose_export_destination() == {"selected": True, "directory": str(chosen)}
    assert app.choose_export_destination() == {"selected": True, "directory": str(chosen)}
    assert len(calls) == 2
    app.start_export(definition(), "new")
    while app.state["phase"] == "exporting" and monotonic() < deadline:
        sleep(0.01)
    assert app.state["phase"] == "exported"
    assert (chosen / "crop_001" / "building_crop_raw.ply").is_file()
    assert not (workspace / "crop_001").exists()


def test_native_export_folder_picker_uses_directory_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(crop_server.shutil, "which", lambda name: "/usr/bin/zenity")
    commands = []

    def fake_dialog(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, str(tmp_path) + "\n", "")

    monkeypatch.setattr(crop_server.subprocess, "run", fake_dialog)
    assert crop_server.pick_export_directory() == tmp_path
    assert commands[0][0] == ["/usr/bin/zenity", "--file-selection", "--directory",
                              "--title=Choose PointFrame export folder"]
    assert commands[0][1]["capture_output"] is True

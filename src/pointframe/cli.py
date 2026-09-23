"""Command line entry point for the local crop viewer."""

import argparse
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .crop_server import run_crop_ui


def default_workspace(source: Path) -> Path:
    source = source.expanduser().resolve(strict=True)
    data_home = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not data_home.is_absolute():
        data_home = Path.home() / ".local" / "share"
    key = hashlib.sha256(os.fsencode(source)).hexdigest()[:12]
    return data_home / "pointframe" / "workspaces" / f"{source.stem}-{key}"


def pick_ply_file() -> Path | None:
    """Ask the Linux desktop for a local PLY path; None means cancelled."""
    zenity = shutil.which("zenity")
    if zenity is None:
        raise RuntimeError("A native file picker requires Zenity; install it or pass a PLY path")
    result = subprocess.run(
        [zenity, "--file-selection", "--title=Select PLY point cloud",
         "--file-filter=PLY files | *.ply *.PLY"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "File picker failed")
    selected = result.stdout.rstrip("\n")
    if not selected:
        return None
    path = Path(selected)
    if path.suffix.lower() != ".ply":
        raise ValueError("Select a .ply file")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crop a binary PLY point cloud in a local browser viewer")
    parser.add_argument("input", nargs="?", type=Path, help="Source binary PLY file")
    parser.add_argument("--output-dir", type=Path, help="Directory for versioned crop exports")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--preview-points", type=int, default=1_500_000)
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser automatically")
    args = parser.parse_args(argv)
    if args.preview_points < 1:
        parser.error("--preview-points must be positive")
    try:
        if args.input is None:
            run_crop_ui(None, None, args.host, args.port, args.preview_points,
                        not args.no_open, args.output_dir, pick_ply_file, default_workspace)
            return 0
        source = args.input.expanduser().resolve(strict=True)
        workspace = default_workspace(source)
        run_crop_ui(source, workspace, args.host, args.port, args.preview_points,
                    not args.no_open, args.output_dir, pick_ply_file, default_workspace)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    return 0

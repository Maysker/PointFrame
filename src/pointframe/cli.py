"""Command line entry point for the local crop viewer."""

import argparse
import hashlib
import os
from pathlib import Path

from .crop_server import run_crop_ui


def default_workspace(source: Path) -> Path:
    source = source.expanduser().resolve(strict=True)
    data_home = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not data_home.is_absolute():
        data_home = Path.home() / ".local" / "share"
    key = hashlib.sha256(os.fsencode(source)).hexdigest()[:12]
    return data_home / "pointframe" / "workspaces" / f"{source.stem}-{key}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crop a binary PLY point cloud in a local browser viewer")
    parser.add_argument("input", type=Path, help="Source binary PLY file")
    parser.add_argument("--output-dir", type=Path, help="Directory for versioned crop exports")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--preview-points", type=int, default=1_500_000)
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser automatically")
    args = parser.parse_args(argv)
    if args.preview_points < 1:
        parser.error("--preview-points must be positive")
    try:
        source = args.input.expanduser().resolve(strict=True)
        workspace = default_workspace(source)
        run_crop_ui(source, workspace, args.host, args.port, args.preview_points,
                    not args.no_open, args.output_dir)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0

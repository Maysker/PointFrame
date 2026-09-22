# PointFrame

PointFrame is a local browser viewer for cropping a binary PLY point cloud. It
keeps the source file unchanged and exports the selected full-resolution points
as both raw and rigidly aligned PLY files.

Install with `python -m pip install -e .` from this directory. Then run either:

```sh
python -m pointframe /path/to/cloud.ply
pointframe /path/to/cloud.ply
```

The browser opens at `http://127.0.0.1:8765`. Use `--no-open` to suppress the
automatic browser launch, or `--host` and `--port` to change the listening
address. `--preview-points` controls the sampled preview size; exports always
read the full source cloud.

Preview and crop-definition files live in a workspace under
`$XDG_DATA_HOME/pointframe/workspaces/<source-name>-<path-hash>/`, or
`~/.local/share/pointframe/workspaces/` when `XDG_DATA_HOME` is unset.
By default, exports live in `~/PointFrame/Exports/<source-stem>/crop_001/`,
then `crop_002/` and so on. With `--output-dir DIR`, exports go to
`DIR/crop_001/`, `DIR/crop_002/`, etc. Existing versions are never silently
overwritten. The option does not move the preview workspace.

The input must be a binary little or big endian PLY with vertex properties
`x`, `y`, `z`, `nx`, `ny`, `nz`, `red`, `green`, and `blue`.

Run the crop tests with `python -m pytest` after installing `.[test]`.

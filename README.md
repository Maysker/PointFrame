# PointFrame

PointFrame is a local browser viewer for cropping a binary PLY point cloud. It
keeps the source file unchanged and exports the selected full-resolution points
as both raw and rigidly aligned PLY files.

Install the optional desktop shell with `python -m pip install -e '.[desktop]'`
from this directory. Then run either:

```sh
pointframe
python -m pointframe /path/to/cloud.ply
pointframe /path/to/cloud.ply
```

With no path, PointFrame starts a local server and opens a start screen in a
PointFrame window. Click **Open point cloud** to choose a `.ply` file with the native Linux
picker. Cancelling returns to the start screen. The picker uses the system's
Zenity command; if it is unavailable, pass the PLY path explicitly. The selected
file is read locally and is not uploaded through the window. Passing a PLY
path starts the viewer directly.

The window displays the existing localhost UI at `http://127.0.0.1:8765`.
Closing it shuts down the local server. If PyWebView or its Linux GTK renderer
cannot start, PointFrame opens that URL in the external browser instead. A
plain `python -m pip install -e .` install uses this browser fallback. Use
`--no-open` to run the server without opening a window or browser, or `--host`
and `--port` to change the listening address. `--preview-points` controls the
sampled preview size; exports always read the full source cloud.

Preview and crop-definition files live in a workspace under
`$XDG_DATA_HOME/pointframe/workspaces/<source-name>-<path-hash>/`, or
`~/.local/share/pointframe/workspaces/` when `XDG_DATA_HOME` is unset.
Without `--output-dir`, clicking **Export full-resolution crop** opens a native
Linux folder picker on the machine running PointFrame. Choose a folder once per
session; exports then go to `<chosen-folder>/crop_001/`, `crop_002/`, and so on.
Cancelling the picker leaves the crop unchanged and starts no export. With
`--output-dir DIR`, exports go directly to `DIR/crop_001/`, `DIR/crop_002/`, etc.,
without showing a folder picker. Existing versions are never silently
overwritten. The chosen folder does not move the preview workspace, and the
source PLY is not uploaded or copied through the browser. The folder picker
uses the system's Zenity command. Each version contains `crop_raw.ply`,
`crop_aligned.ply`, `crop_definition.json`, and `crop_report.json`.

The input must be a binary little or big endian PLY with `x`, `y`, and `z`
vertex properties. `red`, `green`, `blue`, `nx`, `ny`, and `nz` are optional.
Missing colors appear as neutral gray in the preview. Raw and aligned exports
preserve the source properties; aligned exports rotate normals when present
and do not add normals when absent.

Run the crop tests with `python -m pytest` after installing `.[test]`.

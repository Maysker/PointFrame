from pathlib import Path

import numpy as np
import pytest

from pointframe.crop import export_crop, parse_binary_ply, prepare_preview, uvw_to_xyz
from test_crop import definition_v2, rotated_frame


PREVIEW_DTYPE = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                          ("r", "u1"), ("g", "u1"), ("b", "u1")])


@pytest.mark.parametrize("rgb,normals", [
    (False, False), (True, False), (False, True), (True, True),
], ids=["xyz", "xyz_rgb", "xyz_normals", "xyz_rgb_normals"])
def test_optional_ply_properties_preview_crop_and_export(
    tmp_path: Path, rgb: bool, normals: bool,
) -> None:
    fields = [(name, "<f4") for name in ("x", "y", "z")]
    if normals:
        fields += [(name, "<f4") for name in ("nx", "ny", "nz")]
    if rgb:
        fields += [(name, "u1") for name in ("red", "green", "blue")]
    rows = np.zeros(4, dtype=np.dtype(fields))
    local = np.asarray([[1, 1, 1], [1, 1, 3], [3, 1, 1], [.5, .5, .5]], dtype=float)
    world = uvw_to_xyz(local, rotated_frame())
    for axis, name in enumerate(("x", "y", "z")):
        rows[name] = world[:, axis]
    if normals:
        for axis, name in enumerate(("nx", "ny", "nz")):
            rows[name] = np.asarray([[1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 0]])[:, axis]
    if rgb:
        for name, values in zip(("red", "green", "blue"),
                                ([10, 40, 70, 100], [20, 50, 80, 110], [30, 60, 90, 120])):
            rows[name] = values

    properties = "".join(
        f"property {'uchar' if name in ('red', 'green', 'blue') else 'float'} {name}\n"
        for name in rows.dtype.names
    )
    source = tmp_path / "source.ply"
    source.write_bytes(("ply\nformat binary_little_endian 1.0\n"
                        f"element vertex {len(rows)}\n{properties}end_header\n").encode() + rows.tobytes())
    original = source.read_bytes()
    layout = parse_binary_ply(source)
    assert layout.dtype.names == rows.dtype.names

    preview_dir = tmp_path / "preview"
    metadata = prepare_preview(layout, preview_dir, target_points=4)
    preview = np.fromfile(preview_dir / "preview.bin", dtype=PREVIEW_DTYPE)
    assert metadata["preview_point_count"] == len(rows)
    assert metadata["preview_record_bytes"] == 15
    assert len(preview) == len(rows)
    for channel, source_name in (("r", "red"), ("g", "green"), ("b", "blue")):
        assert preview[channel].tolist() == (rows[source_name].tolist() if rgb else [180] * len(rows))

    report = export_crop(layout, tmp_path / "export", definition_v2())
    assert report["output_point_count"] == 2
    raw_layout = parse_binary_ply(Path(report["raw_output_ply"]))
    aligned_layout = parse_binary_ply(Path(report["aligned_output_ply"]))
    assert raw_layout.dtype.names == aligned_layout.dtype.names == rows.dtype.names
    with raw_layout.path.open("rb") as stream:
        stream.seek(raw_layout.data_offset)
        raw = np.fromfile(stream, dtype=raw_layout.dtype, count=raw_layout.vertex_count)
    with aligned_layout.path.open("rb") as stream:
        stream.seek(aligned_layout.data_offset)
        aligned = np.fromfile(stream, dtype=aligned_layout.dtype, count=aligned_layout.vertex_count)
    assert raw.tobytes() == rows[[0, 3]].tobytes()
    assert np.column_stack([aligned[name] for name in ("x", "y", "z")]) == pytest.approx(local[[0, 3]])
    if normals:
        axes = np.asarray([rotated_frame()[name] for name in ("u", "v", "w")])
        expected = np.column_stack([rows[[0, 3]][name] for name in ("nx", "ny", "nz")]) @ axes.T
        assert np.column_stack([aligned[name] for name in ("nx", "ny", "nz")]) == pytest.approx(expected)
    if rgb:
        for name in ("red", "green", "blue"):
            assert aligned[name].tolist() == rows[[0, 3]][name].tolist()
    assert source.read_bytes() == original

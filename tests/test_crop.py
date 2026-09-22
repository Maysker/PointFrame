from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest
import pointframe.crop as crop_module

from pointframe.crop import (
    crop_mask,
    estimate_crop_frame,
    export_crop,
    export_inventory,
    export_versioned,
    flip_top_frame,
    load_definition,
    orient_crop_frame_to_cameras,
    parse_binary_ply,
    points_in_polygon,
    prepare_preview,
    rotate_top_180_frame,
    save_definition,
    uvw_to_xyz,
    validate_crop_frame,
    xyz_to_uvw,
)
from pointframe.utils import sha256_file


DTYPE = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("nx", "<f4"), ("ny", "<f4"),
                  ("nz", "<f4"), ("red", "u1"), ("green", "u1"), ("blue", "u1")])
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_cloud(path: Path, rows: list[tuple]) -> None:
    header = ("ply\nformat binary_little_endian 1.0\ncomment synthetic\n"
              f"element vertex {len(rows)}\nproperty float x\nproperty float y\nproperty float z\n"
              "property float nx\nproperty float ny\nproperty float nz\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n").encode()
    data = np.array(rows, dtype=DTYPE)
    path.write_bytes(header + data.tobytes())


@pytest.fixture
def cloud(tmp_path: Path) -> Path:
    path = tmp_path / "source.ply"
    write_cloud(path, [
        (0, 0, 0, 1, 0, 0, 10, 20, 30),
        (1, 1, 1, 0, 1, 0, 40, 50, 60),
        (2, 2, 2, 0, 0, 1, 70, 80, 90),
        (3, 3, 3, -1, 0, 0, 100, 110, 120),
        (0.5, 0.5, 4, 0, -1, 0, 130, 140, 150),
    ])
    return path


def definition(**changes):
    value = {"schema_version": 1, "projection": "XY", "polygon": [[0, 0], [2, 0], [2, 2], [0, 2]],
             "z_min": 0, "z_max": 2, "keep": "inside", "boundary_included": True}
    value.update(changes)
    return value


def rotated_frame() -> dict:
    root = 2**-0.5
    return {
        "origin": [10.0, -4.0, 2.0],
        "u": [root, root, 0.0],
        "v": [0.0, 0.0, 1.0],
        "w": [root, -root, 0.0],
    }


def definition_v2(**changes):
    value = {
        "schema_version": 2,
        "crop_frame": rotated_frame(),
        "polygon_uv": [[0, 0], [2, 0], [2, 2], [0, 2]],
        "w_min": 0,
        "w_max": 2,
        "keep": "inside",
        "boundary_included": True,
    }
    value.update(changes)
    return value


def test_binary_ply_metadata_parsing(cloud: Path) -> None:
    layout = parse_binary_ply(cloud)
    assert layout.vertex_count == 5
    assert layout.dtype.names == ("x", "y", "z", "nx", "ny", "nz", "red", "green", "blue")
    assert layout.data_offset > 0 and layout.dtype.itemsize == 27


def test_deterministic_preview_generation_and_cache(cloud: Path, tmp_path: Path) -> None:
    layout = parse_binary_ply(cloud)
    first = prepare_preview(layout, tmp_path / "a", target_points=3)
    second = prepare_preview(layout, tmp_path / "b", target_points=3)
    cached = prepare_preview(layout, tmp_path / "a", target_points=3)
    assert first["preview_point_count"] == 3
    assert first["preview_sha256"] == second["preview_sha256"]
    assert cached["cached"] is True
    assert first["method"] == "deterministic_global_index_stride"
    assert validate_crop_frame(first["auto_crop_frame"])
    assert "trimmed PCA" in first["auto_level_method"]
    assert first["auto_w_sign"]["status"] == "unresolved"
    assert "unresolved" in first["auto_level_method"]
    assert len(first["auto_w_bounds"]) == 2
    assert first["auto_w_bounds"][0] <= first["auto_w_bounds"][1]


def test_orbit_target_pan_and_nearest_point_are_deterministic() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable for the deterministic viewer-navigation test")
    navigation = PROJECT_ROOT / "src" / "pointframe" / "crop_web" / "navigation.js"
    script = f"""
const nav=require({json.dumps(str(navigation))});
const topBasis={{right:[1,0,0],up:[0,1,0],depth:[0,0,1]}};
const frontBasis={{right:[1,0,0],up:[0,0,1],depth:[0,-1,0]}};
const neutral={{tiltX:0,tiltY:0,distance:2,target:[1,2,3]}};
const neutralBasis=nav.topTiltBasis(neutral);
const up=nav.topTiltDrag(neutral,0,-100,1000,500),down=nav.topTiltDrag(neutral,0,100,1000,500);
const left=nav.topTiltDrag(neutral,-100,0,1000,500),right=nav.topTiltDrag(neutral,100,0,1000,500);
const diagonal=nav.topTiltDrag(neutral,50,-25,1000,500);
const returned=nav.topTiltDrag(diagonal,-50,25,1000,500);
const limited=nav.topTiltDrag(neutral,100000,-100000,1000,500);
let repeated=neutral;
for(let i=0;i<1000;i++){{repeated=nav.topTiltDrag(repeated,3,-2,1000,500);repeated=nav.topTiltDrag(repeated,-3,2,1000,500)}}
const basis=nav.topTiltBasis(diagonal),repeatedBasis=nav.topTiltBasis(repeated);
const dot=(a,b)=>a.reduce((sum,value,index)=>sum+value*b[index],0);
const determinant=b=>dot(b.right,[b.up[1]*b.depth[2]-b.up[2]*b.depth[1],b.up[2]*b.depth[0]-b.up[0]*b.depth[2],b.up[0]*b.depth[1]-b.up[1]*b.depth[0]]);
const target=nav.panTarget([0,0,0],100,50,1000,500,1,[1,1],frontBasis);
const panStart=[1,2,3],cameraStart=nav.cameraPosition(panStart,diagonal);
const panEnd=nav.panTarget(panStart,80,-30,1000,500,1,[1,1],diagonal);
const cameraEnd=nav.cameraPosition(panEnd,diagonal);
const look=cameraStart.map((value,index)=>panStart[index]-value);
const topFrame=nav.topFrameFromView({{right:[1,0,0],up:[0,1,0],depth:[0,0,1]}});
const topDet=topFrame.u[0]*(topFrame.v[1]*topFrame.w[2]-topFrame.v[2]*topFrame.w[1])-topFrame.u[1]*(topFrame.v[0]*topFrame.w[2]-topFrame.v[2]*topFrame.w[0])+topFrame.u[2]*(topFrame.v[0]*topFrame.w[1]-topFrame.v[1]*topFrame.w[0]);
const tiltedFrame=nav.topFrameFromView(basis);
const positions=new Float32Array([-0.5,0,0, 0.5,0,0]);
const flags=new Float32Array([1,1]);
const nearest=nav.nearestProjectedPoint(positions,flags,'all',[0,0,0],neutral,1000,500,1,[1,1],750,250);
const free=nav.freeOrbitDrag({{basis:frontBasis,target:[1,2,3]}},130,85,1000,500);
const overview={{azimuth:.3,elevation:nav.DEFAULT_OVERVIEW_ELEVATION,distance:3,target:[1,2,3]}};
const overviewHorizontal=nav.overviewDrag(overview,100,0,1000,500),overviewVertical=nav.overviewDrag(overview,0,100,1000,500),overview360=nav.overviewDrag(overview,1000*360/105,0,1000,500),overviewBasis=nav.overviewBasis(overview360);
const overviewHigh=nav.overviewDrag(overview,0,-100000,1000,500),overviewLow=nav.overviewDrag(overview,0,100000,1000,500);
let overviewRepeated=overview;
for(let i=0;i<12;i++)overviewRepeated=nav.overviewDrag(overviewRepeated,1000*360/105,0,1000,500);
const overviewRepeatedBasis=nav.overviewBasis(overviewRepeated);
const fitPositions=new Float32Array([-10,-1,0, 10,1,0, -2,-.5,0, 2,.5,0]),fitFlags=new Float32Array([1,1,0,0]);
const fitAll=nav.fitProjectedBounds(fitPositions,fitFlags,'all',[0,0,0],topBasis,1000,500),fitSelected=nav.fitProjectedBounds(fitPositions,fitFlags,'selected',[0,0,0],topBasis,1000,500),fitExcluded=nav.fitProjectedBounds(fitPositions,fitFlags,'excluded',[0,0,0],topBasis,1000,500);
const smallPositions=new Float32Array(Array.from(fitPositions,value=>value*.1)),fitSmall=nav.fitProjectedBounds(smallPositions,fitFlags,'all',[0,0,0],topBasis,1000,500);
console.log(JSON.stringify({{neutral,neutralBasis,up,down,left,right,diagonal,returned,limited,repeated,basis,repeatedBasis,det:determinant(basis),target,panStart,panEnd,cameraStart,cameraEnd,look,topFrame,topDet,tiltedFrame,nearest,free,maxTilt:nav.MAX_TOP_TILT,overview,overviewHorizontal,overviewVertical,overview360,overviewHigh,overviewLow,overviewBasis,overviewRepeated,overviewRepeatedBasis,overviewDet:determinant(overviewBasis),fixed:nav.FIXED_BASES,fixedDets:{{front:determinant(nav.FIXED_BASES.front),side:determinant(nav.FIXED_BASES.side)}},fitAll,fitSelected,fitExcluded,fitSmall,minOverview:nav.MIN_OVERVIEW_ELEVATION,maxOverview:nav.MAX_OVERVIEW_ELEVATION}}));
"""
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True)
    state = json.loads(result.stdout)
    assert state["target"] == pytest.approx([-0.2, 0.0, 0.2])
    assert state["neutralBasis"] == {"right": [1, 0, 0], "up": [0, 1, 0], "depth": [0, 0, 1]}
    assert state["up"]["tiltY"] == pytest.approx(-state["down"]["tiltY"])
    assert state["left"]["tiltX"] == pytest.approx(-state["right"]["tiltX"])
    assert state["up"]["tiltX"] == state["down"]["tiltX"] == 0
    assert state["left"]["tiltY"] == state["right"]["tiltY"] == 0
    assert state["diagonal"]["tiltX"] > 0 and state["diagonal"]["tiltY"] > 0
    assert state["returned"]["tiltX"] == pytest.approx(0, abs=1e-12)
    assert state["returned"]["tiltY"] == pytest.approx(0, abs=1e-12)
    assert np.hypot(state["limited"]["tiltX"], state["limited"]["tiltY"]) == pytest.approx(state["maxTilt"])
    assert np.degrees(state["maxTilt"]) == pytest.approx(30)
    assert state["repeated"] == state["neutral"]
    assert state["repeatedBasis"] == state["neutralBasis"]
    for axis in ("right", "up", "depth"):
        assert np.linalg.norm(state["basis"][axis]) == pytest.approx(1)
    assert np.dot(state["basis"]["right"], state["basis"]["up"]) == pytest.approx(0, abs=1e-12)
    assert np.dot(state["basis"]["right"], state["basis"]["depth"]) == pytest.approx(0, abs=1e-12)
    assert np.dot(state["basis"]["up"], state["basis"]["depth"]) == pytest.approx(0, abs=1e-12)
    assert state["det"] == pytest.approx(1)
    pan_delta = np.asarray(state["panEnd"]) - state["panStart"]
    camera_delta = np.asarray(state["cameraEnd"]) - state["cameraStart"]
    assert camera_delta == pytest.approx(pan_delta)
    look = np.asarray(state["look"]) / np.linalg.norm(state["look"])
    assert look == pytest.approx(-np.asarray(state["basis"]["depth"]))
    # A camera above the scene looks along -Z; Top uses W=-view direction=+Z.
    assert state["topFrame"]["w"] == pytest.approx([0, 0, 1])
    assert state["topFrame"]["u"] == pytest.approx([1, 0, 0])
    assert state["topDet"] > 0
    assert state["tiltedFrame"]["w"] == pytest.approx(state["basis"]["depth"])
    assert np.linalg.det(np.asarray([state["tiltedFrame"][key] for key in ("u", "v", "w")])) > 0
    assert state["nearest"] == 1
    assert state["free"]["basis"]["depth"][2] < 0
    assert state["overviewHorizontal"]["elevation"] == pytest.approx(state["overview"]["elevation"])
    assert state["overviewHorizontal"]["azimuth"] > state["overview"]["azimuth"]
    assert state["overviewVertical"]["azimuth"] == pytest.approx(state["overview"]["azimuth"])
    assert state["overviewVertical"]["elevation"] < state["overview"]["elevation"]
    assert state["overview360"]["azimuth"] - state["overview"]["azimuth"] == pytest.approx(2 * np.pi)
    assert state["overview360"]["elevation"] == pytest.approx(state["overview"]["elevation"])
    assert state["overviewHigh"]["elevation"] == pytest.approx(state["maxOverview"])
    assert state["overviewLow"]["elevation"] == pytest.approx(state["minOverview"])
    assert state["overviewDet"] == pytest.approx(1)
    assert state["overviewBasis"]["right"][2] == pytest.approx(0, abs=1e-12)
    assert state["overviewBasis"]["up"][2] > 0
    for axis in ("right", "up", "depth"):
        assert state["overviewRepeatedBasis"][axis] == pytest.approx(state["overviewBasis"][axis], abs=1e-11)
    assert state["overviewRepeated"]["elevation"] == pytest.approx(state["overview"]["elevation"])
    assert state["fixed"]["front"] == {"right": [-1, 0, 0], "up": [0, 0, 1], "depth": [0, 1, 0]}
    assert state["fixed"]["side"] == {"right": [0, 1, 0], "up": [0, 0, 1], "depth": [1, 0, 0]}
    assert state["fixedDets"] == pytest.approx({"front": 1, "side": 1})
    assert state["fitAll"]["count"] == 4
    assert state["fitSelected"]["count"] == state["fitExcluded"]["count"] == 2
    assert state["fitExcluded"]["zoom"] > state["fitSelected"]["zoom"]
    assert state["fitSmall"]["zoom"] > state["fitAll"]["zoom"]
    assert state["fitAll"]["fit"] == pytest.approx([.5, 1])
    assert state["fitAll"]["zoom"] * 10 * state["fitAll"]["fit"][0] <= .9200001


def test_polygon_editing_operations_are_deterministic() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable for the deterministic polygon-editor test")
    polygon_module = PROJECT_ROOT / "src" / "pointframe" / "crop_web" / "polygon.js"
    script = f"""
const poly=require({json.dumps(str(polygon_module))});
let arbitrary=[];
for(const point of [[0,0],[2,0],[3,1],[2,2],[0,2],[-1,1]])arbitrary=poly.appendVertex(arbitrary,point);
const square=[[0,0],[2,0],[2,2],[0,2]];
const normal=poly.insertVertex(square,0,[1,0]);
const closing=poly.insertVertex(square,3,[0,1]);
const deleted=poly.deleteVertex(normal,1);
const blocked=poly.deleteVertex([[0,0],[2,0],[1,1]],1);
const nearest=poly.nearestPointOnSegment([6,3],[0,0],[10,0]);
const nearHit=poly.nearestEdgeHit(square,[1,.25],.3);
const farHit=poly.nearestEdgeHit(square,[10,10],.3);
const arbitraryHit=poly.insertAtEdgeHit(square,square,[1.5,.2],.3);
const closingHit=poly.insertAtEdgeHit(square,square,[-.2,1],.3);
const dragged=poly.moveVertex(arbitraryHit.polygon,arbitraryHit.dragVertex,[1.5,-1]);
const selectionBefore=poly.pointInPolygon(square,1.5,-.5,true);
const selectionAfterDrag=poly.pointInPolygon(dragged,1.5,-.5,true);
const usesAllVertices=poly.pointInPolygon([[0,0],[2,0],[2,2],[0,2],[-2,2],[-2,0]],-1,1,true);
console.log(JSON.stringify({{arbitrary,normal,closing,deleted,blocked,nearest,nearHit,farHit,arbitraryHit,closingHit,dragged,selectionBefore,selectionAfterDrag,usesAllVertices}}));
"""
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True)
    state = json.loads(result.stdout)
    assert len(state["arbitrary"]) == 6
    assert state["normal"] == [[0, 0], [1, 0], [2, 0], [2, 2], [0, 2]]
    assert state["closing"] == [[0, 0], [2, 0], [2, 2], [0, 2], [0, 1]]
    assert state["deleted"]["deleted"] is True
    assert state["deleted"]["polygon"] == [[0, 0], [2, 0], [2, 2], [0, 2]]
    assert state["blocked"]["deleted"] is False
    assert len(state["blocked"]["polygon"]) == 3
    assert state["nearest"] == {"point": [6, 0], "t": 0.6, "distance": 3}
    assert state["nearHit"]["edgeIndex"] == 0
    assert state["nearHit"]["point"] == pytest.approx([1, 0])
    assert state["farHit"] is None
    assert state["arbitraryHit"]["polygon"] == [[0, 0], [1.5, 0], [2, 0], [2, 2], [0, 2]]
    assert state["arbitraryHit"]["dragVertex"] == 1
    assert state["closingHit"]["hit"]["edgeIndex"] == 3
    assert state["closingHit"]["polygon"] == [[0, 0], [2, 0], [2, 2], [0, 2], [0, 1]]
    assert state["dragged"][1] == [1.5, -1]
    assert state["selectionBefore"] is False
    assert state["selectionAfterDrag"] is True
    assert state["usesAllVertices"] is True


def test_xy_polygon_and_boundary() -> None:
    x = np.array([1, 0, 3], dtype=float)
    y = np.array([1, 1, 1], dtype=float)
    polygon = [[0, 0], [2, 0], [2, 2], [0, 2]]
    assert points_in_polygon(x, y, polygon, True).tolist() == [True, True, False]
    assert points_in_polygon(x, y, polygon, False).tolist() == [True, False, False]


def test_crop_frame_is_orthonormal_and_transform_round_trips() -> None:
    frame = validate_crop_frame(rotated_frame())
    matrix = np.asarray([frame["u"], frame["v"], frame["w"]])
    assert matrix @ matrix.T == pytest.approx(np.eye(3))
    assert np.linalg.det(matrix) > 0
    xyz = np.asarray([[10, -4, 2], [11.25, 3.5, -8], [-2, 9, 4]], dtype=float)
    assert uvw_to_xyz(xyz_to_uvw(xyz, frame), frame) == pytest.approx(xyz)


def test_auto_level_uses_trimmed_orthonormal_pca() -> None:
    rng = np.random.default_rng(42)
    planar = np.column_stack((rng.uniform(-10, 10, 1000), rng.uniform(-5, 5, 1000), rng.normal(0, .05, 1000)))
    points = np.vstack((planar, [[1000, 1000, 1000], [-900, 800, -700]]))
    frame = estimate_crop_frame(points)
    matrix = np.asarray([frame["u"], frame["v"], frame["w"]])
    assert matrix @ matrix.T == pytest.approx(np.eye(3), abs=1e-6)
    assert np.linalg.det(matrix) > 0
    assert abs(frame["w"][2]) > 0.99


def test_camera_centers_resolve_both_pca_signs_to_same_frame() -> None:
    candidate = validate_crop_frame({
        "origin": [1, 2, 3], "u": [1, 0, 0], "v": [0, 1, 0], "w": [0, 0, 1],
    })
    opposite = flip_top_frame(candidate)
    cameras = np.asarray([[2, 1, 12], [-3, 4, 10], [1, 2, 15], [100, 100, -50]], dtype=float)
    oriented_a, info_a = orient_crop_frame_to_cameras(candidate, cameras)
    oriented_b, info_b = orient_crop_frame_to_cameras(opposite, cameras)
    for component in ("origin", "u", "v", "w"):
        assert oriented_a[component] == pytest.approx(oriented_b[component])
    assert np.median((cameras - oriented_a["origin"]) @ oriented_a["w"]) > 0
    assert info_a["status"] == info_b["status"] == "resolved_camera_centers"
    assert info_a["median_camera_w_score"] == pytest.approx(info_b["median_camera_w_score"])
    assert np.linalg.det(np.asarray([oriented_a["u"], oriented_a["v"], oriented_a["w"]])) > 0


def test_left_handed_frame_is_repaired() -> None:
    frame = rotated_frame()
    frame["v"] = [-value for value in frame["v"]]
    repaired = validate_crop_frame(frame)
    matrix = np.asarray([repaired["u"], repaired["v"], repaired["w"]])
    assert matrix @ matrix.T == pytest.approx(np.eye(3))
    assert np.linalg.det(matrix) > 0


def test_flip_top_is_rotation_not_source_reflection() -> None:
    frame = rotated_frame()
    flipped = flip_top_frame(frame)
    source = np.asarray([[11.5, -2.0, 3.25], [8.0, 7.0, -1.0]])
    before = source.copy()
    original_local = xyz_to_uvw(source, frame)
    flipped_local = xyz_to_uvw(source, flipped)
    assert source == pytest.approx(before)
    assert flipped_local[:, 0] == pytest.approx(-original_local[:, 0])
    assert flipped_local[:, 1] == pytest.approx(original_local[:, 1])
    assert flipped_local[:, 2] == pytest.approx(-original_local[:, 2])
    assert np.linalg.norm(flipped_local, axis=1) == pytest.approx(np.linalg.norm(original_local, axis=1))
    assert np.linalg.det(np.asarray([flipped["u"], flipped["v"], flipped["w"]])) > 0


def test_rotate_top_180_preserves_handedness_and_distances() -> None:
    frame = rotated_frame()
    rotated = rotate_top_180_frame(frame)
    source = np.asarray([[11.5, -2.0, 3.25], [8.0, 7.0, -1.0]])
    original_local = xyz_to_uvw(source, frame)
    rotated_local = xyz_to_uvw(source, rotated)
    assert rotated_local[:, :2] == pytest.approx(-original_local[:, :2])
    assert rotated_local[:, 2] == pytest.approx(original_local[:, 2])
    assert np.linalg.norm(rotated_local, axis=1) == pytest.approx(np.linalg.norm(original_local, axis=1))
    assert np.linalg.det(np.asarray([rotated["u"], rotated["v"], rotated["w"]])) > 0


def test_polygon_and_height_crop_in_rotated_frame() -> None:
    local = np.asarray([[1, 1, 1], [1, 1, 3], [3, 1, 1], [0, 1, 0]], dtype=float)
    world = uvw_to_xyz(local, rotated_frame())
    data = np.zeros(len(world), dtype=DTYPE)
    for axis, name in enumerate(("x", "y", "z")):
        data[name] = world[:, axis]
    assert crop_mask(data, definition_v2()).tolist() == [True, False, False, True]
    # Changing only W excludes the otherwise polygon-contained points.
    assert crop_mask(data, definition_v2(w_min=1.5, w_max=2.5)).tolist() == [False, False, False, False]


def test_z_and_combined_crop_keep_inside_outside(cloud: Path) -> None:
    layout = parse_binary_ply(cloud)
    with cloud.open("rb") as stream:
        stream.seek(layout.data_offset)
        data = np.fromfile(stream, dtype=layout.dtype, count=layout.vertex_count)
    inside = crop_mask(data, definition())
    outside = crop_mask(data, definition(keep="outside"))
    assert inside.tolist() == [True, True, True, False, False]
    assert outside.tolist() == [False, False, False, True, True]


def test_definition_serialization_reload_reproducibility(tmp_path: Path) -> None:
    path = tmp_path / "crop_definition.json"
    save_definition(path, definition())
    loaded = load_definition(path)
    assert loaded == definition()
    assert json.loads(path.read_text())["projection"] == "XY"


def test_crop_frame_definition_serialization_reload(tmp_path: Path) -> None:
    path = tmp_path / "crop_definition.json"
    save_definition(path, definition_v2())
    assert load_definition(path) == definition_v2()
    serialized = json.loads(path.read_text())
    assert serialized["schema_version"] == 2
    assert serialized["crop_frame"] == rotated_frame()


@pytest.mark.parametrize("oriented_frame", [flip_top_frame(rotated_frame()), rotate_top_180_frame(rotated_frame())])
def test_oriented_crop_definition_reload_is_exact(tmp_path: Path, oriented_frame: dict) -> None:
    path = tmp_path / "oriented_crop_definition.json"
    saved = definition_v2(crop_frame=oriented_frame)
    save_definition(path, saved)
    assert load_definition(path) == saved


def test_full_export_preserves_properties_source_and_report(cloud: Path, tmp_path: Path) -> None:
    before_bytes = cloud.read_bytes()
    before_hash = sha256_file(cloud)
    workspace = tmp_path / "crop"
    report = export_crop(parse_binary_ply(cloud), workspace, definition(), before_hash)
    assert cloud.read_bytes() == before_bytes
    assert sha256_file(cloud) == before_hash
    assert report["source_point_count"] == 5
    assert report["output_point_count"] == 3
    output = workspace / "crop_raw.ply"
    out_layout = parse_binary_ply(output)
    with output.open("rb") as stream:
        stream.seek(out_layout.data_offset)
        actual = np.fromfile(stream, dtype=out_layout.dtype, count=3)
    with cloud.open("rb") as stream:
        stream.seek(parse_binary_ply(cloud).data_offset)
        expected = np.fromfile(stream, dtype=DTYPE, count=3)
    assert actual.tobytes() == expected.tobytes()
    assert report["output_sha256"] == sha256_file(output)
    assert json.loads((workspace / "crop_report.json").read_text())["output_point_count"] == 3
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        export_crop(parse_binary_ply(cloud), workspace, definition(), before_hash)


def test_rotated_frame_export_preserves_original_vertex_bytes(tmp_path: Path) -> None:
    local = np.asarray([[1, 1, 1], [1, 1, 3], [3, 1, 1], [.5, .5, .5]], dtype=float)
    world = uvw_to_xyz(local, rotated_frame())
    rows = [(p[0], p[1], p[2], i+.1, i+.2, i+.3, 10+i, 20+i, 30+i) for i, p in enumerate(world)]
    source = tmp_path / "rotated.ply"
    write_cloud(source, rows)
    before = source.read_bytes()
    workspace = tmp_path / "export"
    report = export_crop(parse_binary_ply(source), workspace, definition_v2(), sha256_file(source))
    output = workspace / "crop_raw.ply"
    layout = parse_binary_ply(output)
    with output.open("rb") as stream:
        stream.seek(layout.data_offset)
        retained = np.fromfile(stream, dtype=layout.dtype, count=layout.vertex_count)
    source_data = np.asarray(rows, dtype=DTYPE)
    assert retained.tobytes() == source_data[[0, 3]].tobytes()

    aligned_output = workspace / "crop_aligned.ply"
    aligned_layout = parse_binary_ply(aligned_output)
    with aligned_output.open("rb") as stream:
        stream.seek(aligned_layout.data_offset)
        aligned = np.fromfile(stream, dtype=aligned_layout.dtype, count=aligned_layout.vertex_count)
    aligned_xyz = np.column_stack((aligned["x"], aligned["y"], aligned["z"]))
    expected_xyz = local[[0, 3]]
    assert aligned_xyz == pytest.approx(expected_xyz)
    source_normals = np.column_stack((source_data[[0, 3]]["nx"], source_data[[0, 3]]["ny"], source_data[[0, 3]]["nz"]))
    axes = np.asarray([rotated_frame()[name] for name in ("u", "v", "w")])
    expected_normals = source_normals @ axes.T
    aligned_normals = np.column_stack((aligned["nx"], aligned["ny"], aligned["nz"]))
    assert aligned_normals == pytest.approx(expected_normals)
    for color in ("red", "green", "blue"):
        assert aligned[color].tolist() == source_data[[0, 3]][color].tolist()

    source_distance = np.linalg.norm(world[0] - world[3])
    aligned_distance = np.linalg.norm(aligned_xyz[0] - aligned_xyz[1])
    assert aligned_distance == pytest.approx(source_distance, rel=1e-6)
    assert np.linalg.det(axes) > 0
    assert aligned_distance / source_distance == pytest.approx(1.0, rel=1e-6)
    assert aligned_xyz[0, 2] > aligned_xyz[1, 2]  # roof/local +W is above ground

    assert report["schema_version"] == 2
    assert report["crop_frame"] == rotated_frame()
    assert report["raw_output_ply"] == str(output)
    assert report["raw_output_sha256"] == sha256_file(output)
    assert report["aligned_output_ply"] == str(aligned_output)
    assert report["aligned_output_sha256"] == sha256_file(aligned_output)
    assert report["rigid_transform"]["confirmed"] is True
    assert report["rigid_transform"]["reflection"] is False
    assert report["rigid_transform"]["scale"] == 1.0
    assert report["rigid_transform"]["determinant"] > 0
    assert report["aligned_bounds"]["min"] == pytest.approx(np.min(aligned_xyz, axis=0))
    assert report["aligned_bounds"]["max"] == pytest.approx(np.max(aligned_xyz, axis=0))

    reloaded = load_definition(workspace / "crop_definition.json")
    second_workspace = tmp_path / "export-reloaded"
    export_crop(parse_binary_ply(source), second_workspace, reloaded, sha256_file(source))
    assert (second_workspace / "crop_aligned.ply").read_bytes() == aligned_output.read_bytes()
    assert source.read_bytes() == before


def test_versioned_exports_increment_preserve_legacy_and_never_overwrite(cloud: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy = {
        "building_crop_raw.ply": b"legacy raw",
        "building_crop_aligned.ply": b"legacy aligned",
        "crop_definition.json": b"legacy definition",
        "crop_report.json": b"legacy report",
    }
    for name, payload in legacy.items():
        (workspace / name).write_bytes(payload)

    layout = parse_binary_ply(cloud)
    source_hash = sha256_file(cloud)
    first = export_versioned(layout, workspace, definition(), "new", source_hash)
    first_dir = workspace / "crop_001"
    assert first["export_version"] == "crop_001"
    assert first["export_directory"] == str(first_dir)
    assert {path.name for path in first_dir.iterdir()} == {
        "crop_raw.ply", "crop_aligned.ply", "crop_definition.json", "crop_report.json"
    }
    first_bytes = {path.name: path.read_bytes() for path in first_dir.iterdir()}

    second = export_versioned(layout, workspace, definition(keep="outside"), "new", source_hash)
    assert second["export_version"] == "crop_002"
    assert {path.name: path.read_bytes() for path in first_dir.iterdir()} == first_bytes

    (workspace / "crop_004").mkdir()
    fifth = export_versioned(layout, workspace, definition(), "new", source_hash)
    assert fifth["export_version"] == "crop_005"
    report = json.loads((workspace / "crop_005" / "crop_report.json").read_text())
    assert report["export_version"] == "crop_005"
    assert report["export_directory"] == str(workspace / "crop_005")
    assert report["source_sha256"] == source_hash
    assert report["output_point_count"] == 3
    assert report["crop_frame"] == {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0],
                                    "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0]}
    assert report["polygon"] == definition()["polygon"]
    assert (report["z_min"], report["z_max"]) == (0, 2)
    assert report["raw_output_sha256"] == sha256_file(Path(report["raw_output_ply"]))
    assert report["aligned_output_sha256"] == sha256_file(Path(report["aligned_output_ply"]))
    assert export_inventory(workspace)["legacy_export"] is True
    for name, payload in legacy.items():
        assert (workspace / name).read_bytes() == payload


def test_replace_latest_is_explicit_and_failure_preserves_valid_version(
        cloud: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    layout = parse_binary_ply(cloud)
    source_hash = sha256_file(cloud)
    export_versioned(layout, workspace, definition(), "new", source_hash)
    latest = workspace / "crop_001"

    with pytest.raises(ValueError, match="explicitly"):
        export_versioned(layout, workspace, definition(), "replace", source_hash)
    assert not (workspace / "crop_002").exists()

    replacement = export_versioned(layout, workspace, definition(keep="outside"), "replace_latest", source_hash)
    assert replacement["export_version"] == "crop_001"
    assert replacement["output_point_count"] == 2
    valid_bytes = {path.name: path.read_bytes() for path in latest.iterdir()}

    def fail_export(*args, **kwargs):
        stage = Path(args[1])
        (stage / "partial-output").write_bytes(b"incomplete")
        raise RuntimeError("synthetic replacement failure")

    monkeypatch.setattr(crop_module, "export_crop", fail_export)
    with pytest.raises(RuntimeError, match="synthetic replacement failure"):
        crop_module.export_versioned(layout, workspace, definition(), "replace_latest", source_hash)
    assert {path.name: path.read_bytes() for path in latest.iterdir()} == valid_bytes

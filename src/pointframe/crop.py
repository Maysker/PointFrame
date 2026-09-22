from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from .ply import PLY_TYPES, read_header
from .utils import sha256_file


REQUIRED_PROPERTIES = ("x", "y", "z")
DEFAULT_PREVIEW_COLOR = 180
NUMPY_CODES = {"b": "i1", "B": "u1", "h": "i2", "H": "u2", "i": "i4", "I": "u4", "f": "f4", "d": "f8"}


@dataclass(frozen=True)
class PlyLayout:
    path: Path
    format: str
    vertex_count: int
    properties: tuple[dict[str, Any], ...]
    data_offset: int
    dtype: np.dtype


def parse_binary_ply(path: Path) -> PlyLayout:
    path = path.expanduser().resolve(strict=True)
    with path.open("rb") as stream:
        file_format, elements = read_header(stream)
        offset = stream.tell()
    if file_format not in {"binary_little_endian", "binary_big_endian"}:
        raise ValueError("Crop UI currently requires a binary PLY")
    if not elements or elements[0]["name"] != "vertex":
        raise ValueError("PLY must have the vertex element first")
    vertex = elements[0]
    if any(prop["list"] for prop in vertex["properties"]):
        raise ValueError("Variable-length vertex properties are not supported")
    names = {prop["name"] for prop in vertex["properties"]}
    missing = set(REQUIRED_PROPERTIES) - names
    if missing:
        raise ValueError(f"PLY is missing required vertex properties: {', '.join(sorted(missing))}")
    endian = "<" if file_format == "binary_little_endian" else ">"
    fields = []
    for prop in vertex["properties"]:
        try:
            code = PLY_TYPES[prop["type"]][0]
            fields.append((prop["name"], endian + NUMPY_CODES[code]))
        except KeyError as error:
            raise ValueError(f"Unsupported PLY property type: {prop['type']}") from error
    return PlyLayout(path, file_format, int(vertex["count"]), tuple(vertex["properties"]), offset, np.dtype(fields))


def iter_vertices(layout: PlyLayout, chunk_size: int = 500_000) -> Iterator[tuple[int, np.ndarray]]:
    with layout.path.open("rb") as stream:
        stream.seek(layout.data_offset)
        done = 0
        while done < layout.vertex_count:
            wanted = min(chunk_size, layout.vertex_count - done)
            chunk = np.fromfile(stream, dtype=layout.dtype, count=wanted)
            if len(chunk) != wanted:
                raise ValueError("Unexpected end of binary PLY vertex data")
            yield done, chunk
            done += wanted


def points_in_polygon(x: np.ndarray, y: np.ndarray, polygon: list[list[float]], boundary_included: bool = True) -> np.ndarray:
    if len(polygon) < 3:
        return np.zeros(np.broadcast(x, y).shape, dtype=bool)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    inside = np.zeros(np.broadcast(x, y).shape, dtype=bool)
    boundary = np.zeros_like(inside)
    tolerance = 1e-10
    px = np.asarray([point[0] for point in polygon], dtype=np.float64)
    py = np.asarray([point[1] for point in polygon], dtype=np.float64)
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi, xj, yj = px[i], py[i], px[j], py[j]
        dx, dy = xj - xi, yj - yi
        cross = (x - xi) * dy - (y - yi) * dx
        scale = np.maximum(1.0, np.abs(dx) + np.abs(dy))
        on_line = np.abs(cross) <= tolerance * scale
        within = ((x >= min(xi, xj) - tolerance) & (x <= max(xi, xj) + tolerance) &
                  (y >= min(yi, yj) - tolerance) & (y <= max(yi, yj) + tolerance))
        boundary |= on_line & within
        if yi != yj:
            crosses = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
            inside ^= crosses
        j = i
    return (inside | boundary) if boundary_included else (inside & ~boundary)


def validate_crop_frame(value: Any) -> dict[str, list[float]]:
    if not isinstance(value, dict):
        raise ValueError("crop_frame must be an object")
    try:
        origin = np.asarray(value["origin"], dtype=np.float64)
        axes = [np.asarray(value[name], dtype=np.float64) for name in ("u", "v", "w")]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Crop frame origin and axes must be numeric 3-vectors") from error
    if origin.shape != (3,) or any(axis.shape != (3,) for axis in axes):
        raise ValueError("Crop frame origin and axes must be 3-vectors")
    if not np.all(np.isfinite(np.concatenate([origin, *axes]))):
        raise ValueError("Crop frame values must be finite")
    matrix = np.stack(axes)
    if (np.allclose(matrix @ matrix.T, np.eye(3), atol=1e-5, rtol=0)
            and float(np.linalg.det(matrix)) > 1.0 - 1e-5):
        return {"origin": origin.tolist(), "u": axes[0].tolist(), "v": axes[1].tolist(), "w": axes[2].tolist()}
    # Repair finite, non-degenerate frames from their intended W and horizontal U.
    w_norm = float(np.linalg.norm(axes[2]))
    if w_norm <= 1e-12:
        raise ValueError("Crop frame W axis is degenerate")
    w = axes[2] / w_norm
    u = axes[0] - float(np.dot(axes[0], w)) * w
    u_norm = float(np.linalg.norm(u))
    if u_norm <= 1e-12:
        raise ValueError("Crop frame U axis is parallel to W")
    u /= u_norm
    v = np.cross(w, u)
    v /= np.linalg.norm(v)
    u = np.cross(v, w)
    u /= np.linalg.norm(u)
    repaired = np.stack((u, v, w))
    if (not np.allclose(repaired @ repaired.T, np.eye(3), atol=1e-10, rtol=0)
            or float(np.linalg.det(repaired)) <= 0):
        raise ValueError("Crop frame could not be repaired as a right-handed orthonormal basis")
    return {"origin": origin.tolist(), "u": u.tolist(), "v": v.tolist(), "w": w.tolist()}


def flip_top_frame(crop_frame: dict[str, Any]) -> dict[str, list[float]]:
    """Rotate the frame 180 degrees around V: U and W reverse, V stays fixed."""
    frame = validate_crop_frame(crop_frame)
    return validate_crop_frame({"origin": frame["origin"], "u": -np.asarray(frame["u"]),
                                "v": frame["v"], "w": -np.asarray(frame["w"])})


def rotate_top_180_frame(crop_frame: dict[str, Any]) -> dict[str, list[float]]:
    """Rotate the display plane 180 degrees around W: U and V reverse."""
    frame = validate_crop_frame(crop_frame)
    return validate_crop_frame({"origin": frame["origin"], "u": -np.asarray(frame["u"]),
                                "v": -np.asarray(frame["v"]), "w": frame["w"]})


def xyz_to_uvw(xyz: np.ndarray, crop_frame: dict[str, Any]) -> np.ndarray:
    frame = validate_crop_frame(crop_frame)
    points = np.asarray(xyz, dtype=np.float64)
    origin = np.asarray(frame["origin"])
    axes = np.asarray([frame["u"], frame["v"], frame["w"]])
    return (points - origin) @ axes.T


def uvw_to_xyz(uvw: np.ndarray, crop_frame: dict[str, Any]) -> np.ndarray:
    frame = validate_crop_frame(crop_frame)
    coordinates = np.asarray(uvw, dtype=np.float64)
    origin = np.asarray(frame["origin"])
    axes = np.asarray([frame["u"], frame["v"], frame["w"]])
    return coordinates @ axes + origin


def orient_crop_frame_to_cameras(
    crop_frame: dict[str, Any], camera_centers: np.ndarray | None,
) -> tuple[dict[str, list[float]], dict[str, Any]]:
    """Resolve PCA normal polarity toward the robust reconstructed-camera side."""
    frame = validate_crop_frame(crop_frame)
    try:
        centers = np.asarray(camera_centers if camera_centers is not None else [], dtype=np.float64)
    except (TypeError, ValueError):
        centers = np.empty((0, 3), dtype=np.float64)
    valid = centers.ndim == 2 and centers.shape[1:] == (3,)
    if valid:
        centers = centers[np.all(np.isfinite(centers), axis=1)]
    if not valid or not len(centers):
        return frame, {"status": "unresolved", "method": "deterministic_pca_axis_fallback",
                       "camera_center_count": 0, "median_camera_w_score": None}
    origin, w = np.asarray(frame["origin"]), np.asarray(frame["w"])
    score = float(np.median((centers - origin) @ w))
    if score < 0:
        frame = flip_top_frame(frame)
        score = -score
    status = "resolved_camera_centers" if score > 1e-12 else "unresolved"
    return frame, {"status": status, "method": "median_camera_center_direction",
                   "camera_center_count": len(centers), "median_camera_w_score": score}


def estimate_crop_frame(
    xyz: np.ndarray, camera_centers: np.ndarray | None = None,
    *, return_orientation: bool = False,
) -> dict[str, list[float]] | tuple[dict[str, list[float]], dict[str, Any]]:
    """Estimate a two-stage trimmed-PCA frame and resolve W toward cameras when available."""
    points = np.asarray(xyz, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
        raise ValueError("At least three XYZ preview points are required for auto-level")
    finite = points[np.all(np.isfinite(points), axis=1)]
    if len(finite) < 3:
        raise ValueError("Preview has too few finite points for auto-level")
    center = np.median(finite, axis=0)
    distance = np.linalg.norm(finite - center, axis=1)
    radial_limit = np.quantile(distance, 0.98)
    trimmed = finite[distance <= radial_limit]
    covariance = np.cov(trimmed - np.mean(trimmed, axis=0), rowvar=False)
    _, vectors = np.linalg.eigh(covariance)
    provisional = vectors.T
    projected = (trimmed - center) @ provisional.T
    low, high = np.quantile(projected, [0.01, 0.99], axis=0)
    central = trimmed[np.all((projected >= low) & (projected <= high), axis=1)]
    if len(central) >= 3:
        trimmed = central
    origin = np.mean(trimmed, axis=0)
    _, vectors = np.linalg.eigh(np.cov(trimmed - origin, rowvar=False))
    # Largest spread is U; smallest spread is the scene-height/plane normal W.
    u = vectors[:, 2]
    w = vectors[:, 0]
    for axis in (u, w):
        dominant = int(np.argmax(np.abs(axis)))
        if axis[dominant] < 0:
            axis *= -1
    v = np.cross(w, u)
    v /= np.linalg.norm(v)
    w = np.cross(u, v)
    w /= np.linalg.norm(w)
    frame = validate_crop_frame({"origin": origin, "u": u, "v": v, "w": w})
    frame, orientation = orient_crop_frame_to_cameras(frame, camera_centers)
    return (frame, orientation) if return_orientation else frame


def estimate_crop_frame_from_preview(
    path: Path, camera_centers: np.ndarray | None = None, *, return_orientation: bool = False,
) -> dict[str, list[float]] | tuple[dict[str, list[float]], dict[str, Any]]:
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                      ("r", "u1"), ("g", "u1"), ("b", "u1")])
    records = np.fromfile(path, dtype=dtype)
    xyz = np.column_stack((records["x"], records["y"], records["z"]))
    return estimate_crop_frame(xyz, camera_centers, return_orientation=return_orientation)


def exact_w_bounds(layout: PlyLayout, crop_frame: dict[str, Any]) -> list[float]:
    frame = validate_crop_frame(crop_frame)
    origin = np.asarray(frame["origin"])
    w_axis = np.asarray(frame["w"])
    low, high = float("inf"), float("-inf")
    for _, chunk in iter_vertices(layout):
        xyz = np.column_stack((chunk["x"], chunk["y"], chunk["z"]))
        w = (xyz - origin) @ w_axis
        low, high = min(low, float(np.min(w))), max(high, float(np.max(w)))
    return [low, high]


def crop_mask(chunk: np.ndarray, definition: dict[str, Any]) -> np.ndarray:
    definition = validate_definition(definition)
    if definition["schema_version"] == 1:
        u, v, w = chunk["x"], chunk["y"], chunk["z"]
        polygon, w_min, w_max = definition["polygon"], definition["z_min"], definition["z_max"]
    else:
        xyz = np.column_stack((chunk["x"], chunk["y"], chunk["z"]))
        uvw = xyz_to_uvw(xyz, definition["crop_frame"])
        u, v, w = uvw[:, 0], uvw[:, 1], uvw[:, 2]
        polygon, w_min, w_max = definition["polygon_uv"], definition["w_min"], definition["w_max"]
    mask = points_in_polygon(u, v, polygon, definition["boundary_included"])
    mask &= (w >= w_min) & (w <= w_max)
    return mask if definition["keep"] == "inside" else ~mask


def validate_definition(value: dict[str, Any]) -> dict[str, Any]:
    version = value.get("schema_version")
    if version == 1:
        if value.get("projection") != "XY":
            raise ValueError("Schema version 1 requires XY projection")
        polygon = value.get("polygon")
        low_name, high_name = "z_min", "z_max"
    elif version == 2:
        polygon = value.get("polygon_uv")
        low_name, high_name = "w_min", "w_max"
    else:
        raise ValueError("Crop definition schema_version must be 1 or 2")
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError("Crop polygon must contain at least three vertices")
    try:
        normalized = [[float(point[0]), float(point[1])] for point in polygon]
        range_min, range_max = float(value[low_name]), float(value[high_name])
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ValueError("Crop coordinates and height values must be numeric") from error
    if not all(math.isfinite(v) for point in normalized for v in point) or not all(math.isfinite(v) for v in (range_min, range_max)):
        raise ValueError("Crop coordinates must be finite")
    if range_min > range_max:
        raise ValueError(f"{low_name} cannot exceed {high_name}")
    keep = value.get("keep")
    if keep not in {"inside", "outside"}:
        raise ValueError("keep must be 'inside' or 'outside'")
    common = {"schema_version": version, "keep": keep,
              "boundary_included": bool(value.get("boundary_included", True))}
    if version == 1:
        return {**common, "projection": "XY", "polygon": normalized,
                "z_min": range_min, "z_max": range_max}
    return {**common, "crop_frame": validate_crop_frame(value.get("crop_frame")),
            "polygon_uv": normalized, "w_min": range_min, "w_max": range_max}


def save_definition(path: Path, definition: dict[str, Any]) -> None:
    normalized = validate_definition(definition)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_definition(path: Path) -> dict[str, Any]:
    return validate_definition(json.loads(path.read_text(encoding="utf-8")))


def _output_header(layout: PlyLayout, count: int, comment: str = "exact crop; retained vertex records unchanged") -> bytes:
    lines = ["ply", f"format {layout.format} 1.0", f"comment {comment}", f"element vertex {count}"]
    for prop in layout.properties:
        lines.append(f"property {prop['type']} {prop['name']}")
    lines.extend(["end_header", ""])
    return "\n".join(lines).encode("ascii")


def _definition_frame(definition: dict[str, Any]) -> dict[str, list[float]]:
    if definition["schema_version"] == 2:
        return validate_crop_frame(definition["crop_frame"])
    return {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0],
            "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0]}


def _aligned_records(records: np.ndarray, crop_frame: dict[str, Any]) -> np.ndarray:
    """Return copied records with only XYZ and, when present, normals rotated into the crop frame."""
    aligned = records.copy()
    if not len(aligned):
        return aligned
    frame = validate_crop_frame(crop_frame)
    axes = np.asarray([frame["u"], frame["v"], frame["w"]], dtype=np.float64)
    origin = np.asarray(frame["origin"], dtype=np.float64)
    xyz = np.column_stack((records["x"], records["y"], records["z"]))
    transformed_xyz = (xyz - origin) @ axes.T
    for axis, name in enumerate(("x", "y", "z")):
        aligned[name] = transformed_xyz[:, axis]
    if all(name in (records.dtype.names or ()) for name in ("nx", "ny", "nz")):
        normals = np.column_stack((records["nx"], records["ny"], records["nz"]))
        transformed_normals = normals @ axes.T
        for axis, name in enumerate(("nx", "ny", "nz")):
            aligned[name] = transformed_normals[:, axis]
    return aligned


def _write_output(path: Path, layout: PlyLayout, count: int, body: Path, comment: str) -> None:
    with path.open("xb") as target, body.open("rb") as source:
        target.write(_output_header(layout, count, comment))
        while block := source.read(8 * 1024 * 1024):
            target.write(block)


def export_crop(layout: PlyLayout, workspace: Path, definition: dict[str, Any], source_sha256: str | None = None,
                progress: Callable[[float, str], None] | None = None,
                report_context: dict[str, Any] | None = None) -> dict[str, Any]:
    definition = validate_definition(definition)
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    raw_output = workspace / "building_crop_raw.ply"
    aligned_output = workspace / "building_crop_aligned.ply"
    report_path = workspace / "crop_report.json"
    definition_path = workspace / "crop_definition.json"
    conflicts = [p.name for p in (raw_output, aligned_output, report_path) if p.exists()]
    if conflicts:
        raise FileExistsError(f"Refusing to overwrite existing export: {', '.join(conflicts)}")
    source_before = source_sha256 or sha256_file(layout.path)
    bodies: list[Path] = []
    try:
        for prefix in ("crop-raw-body-", "crop-aligned-body-"):
            fd, body_name = tempfile.mkstemp(prefix=prefix, suffix=".bin", dir=workspace)
            os.close(fd)
            bodies.append(Path(body_name))
    except Exception:
        for body in bodies:
            body.unlink(missing_ok=True)
        raise
    raw_body, aligned_body = bodies
    crop_frame = _definition_frame(definition)
    aligned_min = np.full(3, np.inf)
    aligned_max = np.full(3, -np.inf)
    count = 0
    try:
        with raw_body.open("wb") as raw_target, aligned_body.open("wb") as aligned_target:
            for offset, chunk in iter_vertices(layout):
                selected = chunk[crop_mask(chunk, definition)]
                raw_target.write(selected.tobytes())
                aligned = _aligned_records(selected, crop_frame)
                aligned_target.write(aligned.tobytes())
                if len(aligned):
                    aligned_xyz = np.column_stack((aligned["x"], aligned["y"], aligned["z"])).astype(np.float64)
                    aligned_min = np.minimum(aligned_min, np.min(aligned_xyz, axis=0))
                    aligned_max = np.maximum(aligned_max, np.max(aligned_xyz, axis=0))
                count += len(selected)
                if progress:
                    progress((offset + len(chunk)) / layout.vertex_count, f"Writing raw and aligned points ({offset + len(chunk):,}/{layout.vertex_count:,})")
        _write_output(raw_output, layout, count, raw_body, "exact crop; retained vertex records unchanged")
        _write_output(aligned_output, layout, count, aligned_body,
                      "exact crop aligned by persisted rigid crop frame; scale 1; no reflection")
        if sha256_file(layout.path) != source_before:
            raw_output.unlink(missing_ok=True)
            aligned_output.unlink(missing_ok=True)
            raise RuntimeError("Source PLY changed during export")
        raw_hash = sha256_file(raw_output)
        aligned_hash = sha256_file(aligned_output)
        save_definition(definition_path, definition)
        frame_matrix = np.asarray([crop_frame["u"], crop_frame["v"], crop_frame["w"]])
        logical_directory = Path(report_context["export_directory"]) if report_context else workspace
        report = {
            "schema_version": definition["schema_version"], "source_ply": str(layout.path), "source_sha256": source_before,
            "source_point_count": layout.vertex_count, "output_ply": str(logical_directory / raw_output.name), "output_point_count": count,
            "keep": definition["keep"], "boundary_included": definition["boundary_included"],
            "output_sha256": raw_hash, "raw_output_ply": str(logical_directory / raw_output.name), "raw_output_sha256": raw_hash,
            "aligned_output_ply": str(logical_directory / aligned_output.name), "aligned_output_sha256": aligned_hash,
            "crop_frame": crop_frame,
            "rigid_transform": {"confirmed": True, "scale": 1.0,
                                "determinant": float(np.linalg.det(frame_matrix)), "reflection": False},
            "aligned_bounds": None if count == 0 else {"min": aligned_min.tolist(), "max": aligned_max.tolist()},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if report_context:
            report.update(export_version=report_context["export_version"],
                          export_directory=str(logical_directory))
        if definition["schema_version"] == 1:
            report.update(polygon=definition["polygon"], z_min=definition["z_min"], z_max=definition["z_max"])
        else:
            report.update(polygon_uv=definition["polygon_uv"], w_min=definition["w_min"], w_max=definition["w_max"])
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    except Exception:
        raw_output.unlink(missing_ok=True)
        aligned_output.unlink(missing_ok=True)
        raise
    finally:
        for body in bodies:
            body.unlink(missing_ok=True)


EXPORT_DIRECTORY_PATTERN = re.compile(r"crop_(\d+)$")
LEGACY_EXPORT_FILES = ("building_crop_raw.ply", "building_crop_aligned.ply",
                       "crop_definition.json", "crop_report.json")


def export_inventory(workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    exports_root = workspace
    versions = []
    if exports_root.is_dir():
        for candidate in exports_root.iterdir():
            match = EXPORT_DIRECTORY_PATTERN.fullmatch(candidate.name)
            if match and candidate.is_dir():
                versions.append((int(match.group(1)), candidate))
    versions.sort(key=lambda item: item[0])
    entries = [{"number": number, "version": path.name, "directory": str(path)}
               for number, path in versions]
    legacy_files = [name for name in LEGACY_EXPORT_FILES if (workspace / name).exists()]
    return {"versions": entries, "latest": entries[-1] if entries else None,
            "legacy_export": bool(legacy_files), "legacy_files": legacy_files}


def export_versioned(layout: PlyLayout, workspace: Path, definition: dict[str, Any], action: str,
                     source_sha256: str | None = None,
                     progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    if action not in {"new", "replace_latest"}:
        raise ValueError("Export action must explicitly be 'new' or 'replace_latest'")
    workspace = workspace.expanduser().resolve()
    exports_root = workspace
    exports_root.mkdir(parents=True, exist_ok=True)
    inventory = export_inventory(workspace)

    if action == "new":
        number = (inventory["latest"]["number"] + 1) if inventory["latest"] else 1
        while True:
            version = f"crop_{number:03d}"
            target = exports_root / version
            try:
                target.mkdir()
                break
            except FileExistsError:
                number += 1
        try:
            return export_crop(layout, target, definition, source_sha256, progress,
                               {"export_version": version, "export_directory": str(target)})
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    latest = inventory["latest"]
    if not latest:
        raise ValueError("There is no versioned export to replace")
    version = latest["version"]
    target = Path(latest["directory"])
    stage = Path(tempfile.mkdtemp(prefix=f".{version}-replacement-", dir=exports_root))
    backup: Path | None = None
    try:
        report = export_crop(layout, stage, definition, source_sha256, progress,
                             {"export_version": version, "export_directory": str(target)})
        backup = Path(tempfile.mkdtemp(prefix=f".{version}-backup-", dir=exports_root))
        backup.rmdir()
        os.replace(target, backup)
        try:
            os.replace(stage, target)
        except Exception:
            os.replace(backup, target)
            backup = None
            raise
        shutil.rmtree(backup, ignore_errors=True)
        backup = None
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if backup is not None and backup.exists() and not target.exists():
            os.replace(backup, target)


def prepare_preview(layout: PlyLayout, workspace: Path, target_points: int = 1_500_000,
                    progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    preview = workspace / "preview.bin"
    metadata_path = workspace / "preview_metadata.json"
    source_hash = sha256_file(layout.path)
    if metadata_path.exists() and preview.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected_size = metadata.get("preview_point_count", -1) * 15
        if (metadata.get("source_sha256") == source_hash and metadata.get("target_points") == target_points
                and preview.stat().st_size == expected_size):
            changed = False
            orientation = metadata.get("auto_w_sign", {})
            if ("auto_crop_frame" not in metadata or "auto_w_sign" not in metadata
                    or orientation.get("status") == "resolved_camera_centers"):
                frame, orientation = estimate_crop_frame_from_preview(
                    preview, return_orientation=True,
                )
                metadata["auto_crop_frame"] = frame
                metadata["auto_w_sign"] = orientation
                metadata["auto_level_method"] = (
                    "98%-radial + 1%-axis trimmed PCA; smallest-variance axis is W; "
                    + ("W points toward median reconstructed camera-center direction"
                       if orientation["status"] == "resolved_camera_centers"
                       else "W sign unresolved; deterministic PCA-axis fallback")
                )
                metadata.pop("auto_w_bounds", None)
                changed = True
            repaired_frame = validate_crop_frame(metadata["auto_crop_frame"])
            if repaired_frame != metadata["auto_crop_frame"]:
                metadata["auto_crop_frame"] = repaired_frame
                metadata.pop("auto_w_bounds", None)
                changed = True
            if "auto_w_bounds" not in metadata:
                if progress:
                    progress(0.99, "Calculating exact local height bounds")
                metadata["auto_w_bounds"] = exact_w_bounds(layout, metadata["auto_crop_frame"])
                changed = True
            if changed:
                metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            metadata["cached"] = True
            return metadata
    stride = max(1, math.ceil(layout.vertex_count / target_points))
    bounds_min = np.full(3, np.inf)
    bounds_max = np.full(3, -np.inf)
    preview_count = 0
    digest = hashlib.sha256()
    temporary = preview.with_suffix(".bin.tmp")
    with temporary.open("wb") as target:
        for offset, chunk in iter_vertices(layout):
            for axis, name in enumerate(("x", "y", "z")):
                values = chunk[name]
                bounds_min[axis] = min(bounds_min[axis], float(np.min(values)))
                bounds_max[axis] = max(bounds_max[axis], float(np.max(values)))
            first = (-offset) % stride
            sampled = chunk[first::stride]
            packed = np.empty(len(sampled), dtype=np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                                             ("r", "u1"), ("g", "u1"), ("b", "u1")]))
            for name in ("x", "y", "z"):
                packed[name] = sampled[name]
            for source_name, target_name in (("red", "r"), ("green", "g"), ("blue", "b")):
                packed[target_name] = (sampled[source_name] if source_name in sampled.dtype.names
                                       else DEFAULT_PREVIEW_COLOR)
            raw = packed.tobytes()
            target.write(raw)
            digest.update(raw)
            preview_count += len(sampled)
            if progress:
                progress((offset + len(chunk)) / layout.vertex_count, f"Preparing preview ({offset + len(chunk):,}/{layout.vertex_count:,})")
    os.replace(temporary, preview)
    metadata = {
        "schema_version": 1, "source_ply": str(layout.path), "source_sha256": source_hash,
        "source_point_count": layout.vertex_count, "source_size_bytes": layout.path.stat().st_size,
        "preview_file": preview.name, "preview_format": "xyz_float32_le_rgb_uint8_interleaved",
        "preview_record_bytes": 15, "preview_point_count": preview_count, "preview_sha256": digest.hexdigest(),
        "method": "deterministic_global_index_stride", "stride": stride, "target_points": target_points,
        "bounds": {"min": bounds_min.tolist(), "max": bounds_max.tolist()}, "created_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
    }
    frame, orientation = estimate_crop_frame_from_preview(preview, return_orientation=True)
    metadata["auto_crop_frame"] = frame
    metadata["auto_w_sign"] = orientation
    metadata["auto_level_method"] = (
        "98%-radial + 1%-axis trimmed PCA; smallest-variance axis is W; "
        + ("W points toward median reconstructed camera-center direction"
           if orientation["status"] == "resolved_camera_centers"
           else "W sign unresolved; deterministic PCA-axis fallback")
    )
    if progress:
        progress(0.99, "Calculating exact local height bounds")
    metadata["auto_w_bounds"] = exact_w_bounds(layout, metadata["auto_crop_frame"])
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata

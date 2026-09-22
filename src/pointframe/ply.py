"""Binary PLY header fields used by the crop tool."""

from typing import Any, BinaryIO


PLY_TYPES = {
    "char": ("b", 1), "int8": ("b", 1),
    "uchar": ("B", 1), "uint8": ("B", 1),
    "short": ("h", 2), "int16": ("h", 2),
    "ushort": ("H", 2), "uint16": ("H", 2),
    "int": ("i", 4), "int32": ("i", 4),
    "uint": ("I", 4), "uint32": ("I", 4),
    "float": ("f", 4), "float32": ("f", 4),
    "double": ("d", 8), "float64": ("d", 8),
}


def read_header(stream: BinaryIO) -> tuple[str, list[dict[str, Any]]]:
    if stream.readline().strip() != b"ply":
        raise ValueError("Not a PLY file")
    file_format = None
    elements: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    while True:
        raw = stream.readline()
        if not raw:
            raise ValueError("PLY header ended before end_header")
        try:
            line = raw.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise ValueError("PLY header is not ASCII") from error
        parts = line.split()
        if not parts or parts[0] in {"comment", "obj_info"}:
            continue
        if parts[0] == "format":
            file_format = parts[1]
        elif parts[0] == "element":
            current = {"name": parts[1], "count": int(parts[2]), "properties": []}
            elements.append(current)
        elif parts[0] == "property":
            if current is None:
                raise ValueError("PLY property appears before any element")
            if parts[1] == "list":
                current["properties"].append(
                    {"name": parts[4], "list": True, "count_type": parts[2], "item_type": parts[3]}
                )
            else:
                current["properties"].append({"name": parts[2], "list": False, "type": parts[1]})
        elif parts[0] == "end_header":
            break
    if file_format not in {"ascii", "binary_little_endian", "binary_big_endian"}:
        raise ValueError(f"Unsupported PLY format: {file_format}")
    return file_format, elements

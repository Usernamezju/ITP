"""Conservative local validation before treating a paid modeling run as valid."""

import json
import math
import struct
from pathlib import Path


def valid_mesh(path: Path, format: str) -> bool:
    try:
        if format.upper() == "OBJ":
            vertices = 0
            faces = []
            for line in path.read_text(errors="strict").splitlines():
                parts = line.split()
                if parts and parts[0] == "v" and len(parts) >= 4:
                    values = [float(value) for value in parts[1:4]]
                    if not all(math.isfinite(value) for value in values):
                        return False
                    vertices += 1
                if parts and parts[0] == "f" and len(parts) >= 4:
                    indices = [int(value.split("/")[0]) for value in parts[1:]]
                    if 0 in indices or len(set(indices)) < 3:
                        return False
                    # Negative indices refer to vertices present at this line.
                    if any(index < 0 and -index > vertices for index in indices):
                        return False
                    faces.append(indices)
            return (
                vertices >= 3
                and bool(faces)
                and all(index <= vertices for face in faces for index in face)
            )
        if format.upper() != "GLB" or path.stat().st_size > 512 * 1024 * 1024:
            return False
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12:
                return False
            magic, version, length = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2 or length != path.stat().st_size:
                return False
            document = None
            binary_length = 0
            while stream.tell() < length:
                chunk_header = stream.read(8)
                if len(chunk_header) != 8:
                    return False
                size, kind = struct.unpack("<II", chunk_header)
                if size % 4 or stream.tell() + size > length:
                    return False
                if kind == 0x4E4F534A:
                    if document is not None or stream.tell() != 20 or size > 16 * 1024 * 1024:
                        return False
                    document = json.loads(stream.read(size))
                else:
                    if kind == 0x004E4942:
                        binary_length += size
                    stream.seek(size, 1)
        if not isinstance(document, dict) or document.get("asset", {}).get("version") != "2.0":
            return False
        buffers = document.get("buffers", [])
        if len(buffers) != 1 or any("uri" in buffer for buffer in buffers):
            return False
        if any(
            type(buffer["byteLength"]) is not int or buffer["byteLength"] < 0 for buffer in buffers
        ):
            return False
        if sum(buffer["byteLength"] for buffer in buffers) > binary_length:
            return False
        views = document.get("bufferViews", [])
        for view in views:
            index, offset, size = (
                view.get("buffer", 0),
                view.get("byteOffset", 0),
                view["byteLength"],
            )
            if any(type(value) is not int or value < 0 for value in (index, offset, size)):
                return False
            if index >= len(buffers) or offset + size > buffers[index]["byteLength"]:
                return False
        accessors = document.get("accessors", [])
        for mesh in document.get("meshes", []):
            for primitive in mesh.get("primitives", []):
                position = accessors[primitive["attributes"]["POSITION"]]
                if (
                    position["type"] == "VEC3"
                    and position["count"] >= 3
                    and primitive.get("mode", 4) in {4, 5, 6}
                ):
                    compression = primitive.get("extensions", {}).get("KHR_draco_mesh_compression")
                    if compression:
                        return views[compression["bufferView"]]["byteLength"] > 0
                    if "bufferView" not in position:
                        return False
                    view = views[position["bufferView"]]
                    component_size = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5126: 4}.get(
                        position["componentType"]
                    )
                    if not component_size:
                        return False
                    stride = view.get("byteStride", component_size * 3)
                    offset = position.get("byteOffset", 0)
                    if (
                        type(stride) is not int
                        or stride < component_size * 3
                        or type(offset) is not int
                        or offset < 0
                    ):
                        return False
                    if (
                        offset + (position["count"] - 1) * stride + component_size * 3
                        > view["byteLength"]
                    ):
                        return False
                    if "indices" in primitive and accessors[primitive["indices"]]["count"] < 3:
                        return False
                    return True
        return False
    except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error, UnicodeError):
        return False

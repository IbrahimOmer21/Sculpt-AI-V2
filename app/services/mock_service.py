"""Offline stand-in for meshy_service (USE_MOCK_AI=1) so the UI can be developed without spending credits.

Same function names and return values as meshy_service. Writes a small gradient PNG and a pyramid GLB/STL.
"""
import json
import os
import struct
import time
import uuid
import zlib

from flask import current_app

DELAY = 1.5  # seconds, so loading states are visible


def _save(filename, data):
    with open(os.path.join(current_app.config["UPLOAD_DIR"], filename), "wb") as f:
        f.write(data)
    return filename


def _png(size=256, seed=0):
    rows = b"".join(
        b"\x00" + bytes(
            v for x in range(size) for v in ((x + seed) % 256, (y * 255) // size, 200)
        )
        for y in range(size)
    )
    chunk = lambda tag, body: struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


# Square pyramid: 5 points, 6 triangles.
POINTS = [(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1), (0, 1.6, 0)]
FACES = [(0, 1, 2), (0, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0)]


def _glb():
    positions = b"".join(struct.pack("<3f", *p) for p in POINTS)
    indices = b"".join(struct.pack("<3H", *f) for f in FACES)
    indices += b"\x00" * (-len(indices) % 4)
    gltf = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1, 0.76, 0, 1], "roughnessFactor": 0.6}}],
        "buffers": [{"byteLength": len(positions) + len(indices)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(FACES) * 6, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(POINTS), "type": "VEC3",
             "min": [-1, 0, -1], "max": [1, 1.6, 1]},
            {"bufferView": 1, "componentType": 5123, "count": len(FACES) * 3, "type": "SCALAR"},
        ],
    }
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    binary = positions + indices
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _stl():
    tri = "\n".join(
        "facet normal 0 0 0\nouter loop\n" + "".join("vertex %g %g %g\n" % POINTS[i] for i in f) + "endloop\nendfacet"
        for f in FACES
    )
    return f"solid mock\n{tri}\nendsolid mock\n".encode()


def generate_image(prompt):
    time.sleep(DELAY)
    task_id = f"mock-{uuid.uuid4()}"
    return {"image_url": _save(f"{task_id}.png", _png(seed=len(prompt) * 37)), "task_id": task_id}


def refine_image(prompt, previous_image_path):
    return generate_image(prompt)


def generate_sculpture(image_path):
    time.sleep(DELAY * 2)
    task_id = f"mock-{uuid.uuid4()}"
    return {"sculpture_url": _save(f"{task_id}.glb", _glb()), "stl_url": _save(f"{task_id}.stl", _stl())}

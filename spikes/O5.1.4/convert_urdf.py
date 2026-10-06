"""Deterministic display-only URDF/STL conversion. No physics import or network."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET


def numbers(value, default, size=3):
    result = [float(x) for x in (value or default).split()]
    if len(result) != size or not all(math.isfinite(x) for x in result):
        raise ValueError("Invalid finite vector")
    return result


def origin(node):
    item = node.find("origin")
    return {"xyz": numbers(item.get("xyz") if item is not None else None, "0 0 0"),
            "rpy": numbers(item.get("rpy") if item is not None else None, "0 0 0")}


def unity_position(vector):
    x, y, z = vector
    return -y, z, x


def stl_triangles(data):
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + count * 50 == len(data):
            for index in range(count):
                values = struct.unpack_from("<12fH", data, 84 + index * 50)
                yield tuple(values[3:6]), tuple(values[6:9]), tuple(values[9:12])
            return
    vertices = []
    for line in data.decode("ascii").splitlines():
        fields = line.strip().split()
        if fields and fields[0] == "vertex":
            vertices.append(tuple(numbers(" ".join(fields[1:]), "")))
    if not vertices or len(vertices) % 3:
        raise ValueError("Malformed or empty STL")
    for index in range(0, len(vertices), 3):
        yield tuple(vertices[index:index + 3])


def write_mesh(source, target):
    data = source.read_bytes()
    triangles = list(stl_triangles(data))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        handle.write(b"AVM1")
        handle.write(struct.pack("<I", len(triangles)))
        for triangle in triangles:
            # Reflection changes handedness: reverse winding once after C*p.
            values = [value for index in (0, 2, 1) for value in unity_position(triangle[index])]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Nonfinite STL vertex")
            handle.write(struct.pack("<9f", *values))
    return {"source_sha256": hashlib.sha256(data).hexdigest(), "triangles": len(triangles),
            "converted_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def safe_mesh_path(root, relative):
    if relative.startswith("package://"):
        relative = relative.removeprefix("package://g1_description/")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("Mesh path escapes source root")
    if candidate.suffix.lower() != ".stl":
        raise ValueError("Only STL visual meshes are supported")
    return candidate


def convert(urdf, mesh_root, output):
    data = urdf.read_bytes()
    root = ET.fromstring(data)
    materials = {item.get("name"): numbers(item.find("color").get("rgba"), "1 1 1 1", 4)
                 for item in root.findall("material") if item.find("color") is not None}
    output.mkdir(parents=True, exist_ok=True)
    links, joints, meshes = [], [], {}
    for link in sorted(root.findall("link"), key=lambda item: item.get("name")):
        visuals = []
        for visual in link.findall("visual"):
            geometry = visual.find("geometry/mesh")
            if geometry is None:
                raise ValueError("Only mesh visuals are supported")
            relative = geometry.get("filename")
            source = safe_mesh_path(mesh_root, relative)
            mesh_name = source.stem + ".avmesh"
            if relative not in meshes:
                stats = write_mesh(source, output / "meshes" / mesh_name)
                meshes[relative] = dict(source=relative, file="meshes/" + mesh_name, **stats)
            material = visual.find("material")
            color = materials.get(material.get("name"), [1, 1, 1, 1]) if material is not None else [1, 1, 1, 1]
            if material is not None and material.find("color") is not None:
                color = numbers(material.find("color").get("rgba"), "1 1 1 1", 4)
            visuals.append(dict(origin=origin(visual), mesh_file="meshes/" + mesh_name,
                                scale=numbers(geometry.get("scale"), "1 1 1"), color=color))
        links.append(dict(name=link.get("name"), visuals=visuals))
    for joint in sorted(root.findall("joint"), key=lambda item: item.get("name")):
        kind = joint.get("type")
        if kind not in {"fixed", "revolute"} or joint.find("mimic") is not None:
            raise ValueError("Unsupported joint or mimic: " + joint.get("name"))
        axis = joint.find("axis")
        vector = numbers(axis.get("xyz") if axis is not None else None, "1 0 0")
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError("Zero joint axis")
        limits = joint.find("limit")
        lower, upper = (float(limits.get("lower")), float(limits.get("upper"))) if kind == "revolute" else (0, 0)
        if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
            raise ValueError("Invalid joint limits")
        joints.append(dict(name=joint.get("name"), type=kind, parent=joint.find("parent").get("link"),
                           child=joint.find("child").get("link"), origin=origin(joint),
                           axis=[value / norm for value in vector], lower=lower, upper=upper))
    names = {item["name"] for item in links}
    if len(names) != len(links) or len({item["name"] for item in joints}) != len(joints):
        raise ValueError("Duplicate link/joint names")
    children = [item["child"] for item in joints]
    if len(set(children)) != len(children):
        raise ValueError("Multiple parents")
    roots = names - set(children)
    if len(roots) != 1:
        raise ValueError("URDF must have exactly one root")
    visited = set(roots)
    while True:
        prior = len(visited)
        for joint in joints:
            if joint["parent"] not in names or joint["child"] not in names:
                raise ValueError("Unknown link")
            if joint["parent"] in visited:
                visited.add(joint["child"])
        if len(visited) == prior:
            break
    if visited != names:
        raise ValueError("Disconnected or cyclic hierarchy")
    description = dict(schema_version=1, robot=root.get("name"), source_sha256=hashlib.sha256(data).hexdigest(),
                       coordinate_convention="URDF origins/axes; AVM1 vertices in Unity (-y,z,x), winding reversed",
                       root_link=next(iter(roots)), links=links, joints=joints,
                       meshes=[meshes[key] for key in sorted(meshes)])
    payload = json.dumps(description, sort_keys=True, indent=2, allow_nan=False) + "\n"
    result = output / "g1_description.json"
    result.write_text(payload, encoding="utf-8", newline="\n")
    return description, hashlib.sha256(result.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urdf", type=Path)
    parser.add_argument("--mesh-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    description, digest = convert(args.urdf, args.mesh_root, args.output)
    print(json.dumps(dict(description_sha256=digest, links=len(description["links"]),
                          revolute_joints=sum(j["type"] == "revolute" for j in description["joints"]),
                          unique_meshes=len(description["meshes"]),
                          unique_mesh_triangles=sum(m["triangles"] for m in description["meshes"])), indent=2))


if __name__ == "__main__":
    main()

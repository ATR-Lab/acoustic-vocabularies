"""Fetch exact BSD-3 Unitree visual meshes into ignored external-assets."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
import xml.etree.ElementTree as ET

REVISION = "5994d4faef0a9cadd3287f8de0199a67eeb2a259"
BASE = f"https://raw.githubusercontent.com/unitreerobotics/unitree_ros/{REVISION}/robots/g1_description/"
ROOT = Path(__file__).resolve().parents[2]
URDF = ROOT / "unity/Assets/ThirdParty/Unitree/G1/g1_29dof_with_hand_rev_1_0.urdf"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "external-assets/unitree-g1")
    args = parser.parse_args()
    output = args.output.resolve()
    files = sorted({item.get("filename") for item in ET.parse(URDF).findall(".//visual/geometry/mesh")})
    manifest_path = ROOT / "unity/Assets/ThirdParty/Unitree/G1/mesh-sources.json"
    expected = {item["file"]: item for item in json.loads(manifest_path.read_text())["meshes"]} if manifest_path.exists() else {}

    def fetch(relative):
        target = (output / relative).resolve()
        if not target.is_relative_to(output) or not relative.startswith("meshes/"):
            raise ValueError("Invalid mesh path")
        if not target.exists():
            with urlopen(BASE + relative, timeout=60) as response:
                data = response.read()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if expected and (relative not in expected or expected[relative]["sha256"] != digest):
            raise ValueError("Mesh checksum mismatch: " + relative)
        return dict(file=relative, sha256=digest, bytes=len(data))

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(fetch, files))
    manifest = dict(repository="unitreerobotics/unitree_ros", revision=REVISION, license="BSD-3-Clause", meshes=records)
    # This generated audit belongs alongside fetched assets until deliberately reviewed.
    (output / "mesh-sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Verified {len(records)} meshes ({sum(r['bytes'] for r in records)} bytes) at pinned revision {REVISION}")


if __name__ == "__main__":
    main()

"""Read authored sensor frame properties from the pinned USD without physics/DDS."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.Open(str(args.asset))
    if not stage:
        raise RuntimeError("USD stage could not be opened")
    rows = []
    for prim in stage.Traverse():
        if any(name in str(prim.GetPath()).lower() for name in ("mid360", "hand_camera")):
            rows.append({"path": str(prim.GetPath()), "type": prim.GetTypeName(),
                         "attributes": {a.GetName(): str(a.Get()) for a in prim.GetAuthoredAttributes()
                                        if any(key in a.GetName() for key in ("local", "xform"))},
                         "relationships": {r.GetName(): [str(p) for p in r.GetTargets()]
                                           for r in prim.GetRelationships()}})
    result = {"source_asset": args.asset.name, "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
              "metres_per_unit": UsdGeom.GetStageMetersPerUnit(stage), "frames": rows}
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()

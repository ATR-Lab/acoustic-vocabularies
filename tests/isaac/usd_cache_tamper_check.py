"""Actual pinned-USD cache tamper matrix; no simulator, network or GPU needed.

Run only in the approved runtime after independent measurements finish. The
result is structural evidence, not a timing or experiment acceptance result.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade
from isaac.workcell.build_usd import build_workcell
from isaac.workcell.layout import canonical_bytes, neutral_layout
from isaac.workcell.state import StateAccessors

ROOT = "/World/Workcell"
OBJECT = ROOT + "/Objects/tray_A"


def property_recreate(stage, access, folder):
    prim = stage.GetPrimAtPath(OBJECT)
    value = prim.GetAttribute("workcell:enabled").Get()
    prim.RemoveProperty("workcell:enabled")
    prim.CreateAttribute("workcell:enabled", Sdf.ValueTypeNames.Bool).Set(value)


def property_retype(stage, access, folder):
    prim = stage.GetPrimAtPath(OBJECT)
    prim.RemoveProperty("workcell:enabled")
    prim.CreateAttribute("workcell:enabled", Sdf.ValueTypeNames.Int).Set(1)


def prim_recreate(stage, access, folder):
    # Recreate the exact authored subtree; valid-looking replacement handles
    # cannot clear the already-latched resync event.
    copy = Sdf.Layer.CreateAnonymous()
    Sdf.CopySpec(stage.GetRootLayer(), OBJECT, copy, "/backup")
    stage.RemovePrim(OBJECT)
    Sdf.CopySpec(copy, "/backup", stage.GetRootLayer(), OBJECT)


def material_binding(stage, access, folder):
    prim = stage.GetPrimAtPath(OBJECT + "/Visual/Bottom")
    if not prim:
        prim = next(p for p in Usd.PrimRange(stage.GetPrimAtPath(OBJECT)) if p.IsA(UsdGeom.Gprim))
    material = UsdShade.Material(stage.GetPrimAtPath(ROOT + "/Materials/ink"))
    UsdShade.MaterialBindingAPI(prim).Bind(material)


def sublayers(stage, access, folder, *, restore=False):
    layer = Sdf.Layer.CreateAnonymous()
    original = list(stage.GetRootLayer().subLayerPaths)
    stage.GetRootLayer().subLayerPaths = original + [layer.identifier]
    if restore:
        stage.GetRootLayer().subLayerPaths = original


def sublayer_reorder(stage, access, folder):
    original = list(stage.GetRootLayer().subLayerPaths)
    stage.GetRootLayer().subLayerPaths = list(reversed(original))


def session_content(stage, access, folder):
    layer = Sdf.Layer.CreateAnonymous()
    original = list(stage.GetSessionLayer().subLayerPaths)
    stage.GetSessionLayer().subLayerPaths = original + [layer.identifier]
    stage.GetSessionLayer().subLayerPaths = original


def mute(stage, access, folder):
    identifier = stage.GetRootLayer().subLayerPaths[0]
    stage.MuteLayer(identifier)
    stage.UnmuteLayer(identifier)


def edit_target(stage, access, folder):
    previous = stage.GetEditTarget()
    stage.SetEditTarget(stage.GetSessionLayer())
    stage.SetEditTarget(previous)


def layer_identifier(stage, access, folder):
    prior = Path(stage.GetRootLayer().identifier).stem
    stage.GetRootLayer().identifier = str(folder / (prior + "-renamed.usda"))


def import_changed(stage, access, folder, *, session=False):
    layer = stage.GetSessionLayer() if session else stage.GetRootLayer()
    replacement = Sdf.Layer.CreateAnonymous("replacement.usda")
    assert replacement.ImportFromString(layer.ExportToString())
    replacement.customLayerData = {"cache_tamper_diagnostic": 1}
    assert layer.ImportFromString(replacement.ExportToString())


def import_identical(stage, access, folder, *, session=False):
    """Prove a pinned-USD no-op separately from actual mutation rejection."""
    layer = stage.GetSessionLayer() if session else stage.GetRootLayer()
    source = layer.ExportToString()
    before = access.read_state()
    spec = layer.GetPrimAtPath(OBJECT)
    handle = stage.GetPrimAtPath(OBJECT).GetAttribute("workcell:enabled")
    prior = handle.Get()
    notices = []
    def observed(notice, sender):
        row = {"type": type(notice).__name__}
        if hasattr(notice, "GetResyncedPaths"):
            row["resync"] = [str(path) for path in notice.GetResyncedPaths()]
            row["info"] = [str(path) for path in notice.GetChangedInfoOnlyPaths()]
        notices.append(row)
    registrations = [Tf.Notice.RegisterGlobally(kind, observed) for kind in (
        Sdf.Notice.LayersDidChange, Sdf.Notice.LayerDidReplaceContent,
        Sdf.Notice.LayerDidReloadContent, Sdf.Notice.LayerInfoDidChange,
        Usd.Notice.ObjectsChanged)]
    try:
        imported = layer.ImportFromString(source)
    finally:
        for registration in registrations:
            registration.Revoke()
    result = dict(imported=imported, bytes_unchanged=layer.ExportToString() == source,
                  layer_had_prim_spec=bool(spec), prim_spec_equal=spec == layer.GetPrimAtPath(OBJECT),
                  attribute_handle_equal=handle == stage.GetPrimAtPath(OBJECT).GetAttribute("workcell:enabled"),
                  state_unchanged=access.read_state() == before, notices=notices)
    handle.Set(not prior)
    result["retained_and_fresh_live_values_agree"] = (
        handle.Get() == (not prior) and
        stage.GetPrimAtPath(OBJECT).GetAttribute("workcell:enabled").Get() == (not prior) and
        access.read_state()["tray_A"]["enabled"] == (not prior) and
        access.read_public_state()["tray_A"]["enabled"] == (not prior))
    handle.Set(prior)
    result["restored_state_matches"] = access.read_state() == before
    result["passed"] = all(result[key] for key in (
        "imported", "bytes_unchanged", "prim_spec_equal", "attribute_handle_equal",
        "state_unchanged", "retained_and_fresh_live_values_agree", "restored_state_matches")) and not notices
    return result


def missing_binding(stage, access, folder):
    # Report the pinned binding inventory separately; failure to opt in is never
    # interpreted as a cache pass or a silent fallback to weaker checks.
    from pxr import Tf
    return {
        "usd_version": list(Usd.GetVersion()),
        "usd_notices": {name: hasattr(Usd.Notice, name) for name in
                        ("ObjectsChanged", "StageEditTargetChanged", "LayerMutingChanged")},
        "sdf_notices": {name: hasattr(Sdf.Notice, name) for name in
                        ("LayerDidReplaceContent", "LayerDidReloadContent", "LayerIdentifierDidChange",
                         "LayerInfoDidChange", "LayerMutenessChanged")},
        "objects_changed_fields": hasattr(Usd.Notice.ObjectsChanged, "GetChangedFields"),
        "global_notice_registration": hasattr(Tf.Notice, "RegisterGlobally"),
        "layer_muteness_path": hasattr(getattr(Sdf.Notice, "LayerMutenessChanged", None), "layerPath"),
    }


def run(output):
    if output.exists():
        raise FileExistsError("Preserve prior tamper evidence: " + str(output))
    layout = neutral_layout()
    base = Usd.Stage.CreateInMemory()
    original = build_workcell(base, layout)
    baseline = original.read_state()
    source = base.GetRootLayer().ExportToString()
    original.close()
    cases = {
        "property_delete_recreate": property_recreate,
        "property_retype": property_retype,
        "prim_delete_recreate": prim_recreate,
        "semantic_op_order": lambda s,a,f: s.GetPrimAtPath(OBJECT).GetAttribute("xformOpOrder").Set(["xformOp:orient", "xformOp:translate"]),
        "ancestor_transform": lambda s,a,f: UsdGeom.Xformable(s.GetPrimAtPath(ROOT)).AddTranslateOp().Set(Gf.Vec3d(.001,0,0)),
        "ancestor_hide": lambda s,a,f: UsdGeom.Imageable(s.GetPrimAtPath(ROOT)).GetVisibilityAttr().Set("invisible"),
        "property_connection": lambda s,a,f: s.GetPrimAtPath(OBJECT).GetAttribute("workcell:enabled").AddConnection(OBJECT + ".other"),
        "property_timesample": lambda s,a,f: s.GetPrimAtPath(OBJECT).GetAttribute("workcell:enabled").Set(False, Usd.TimeCode(1)),
        "material_binding": material_binding,
        "root_import_same_content": import_identical,
        "root_import_changed_content": import_changed,
        "root_reload_same_content": lambda s,a,f: s.GetRootLayer().Reload(True),
        "session_import_same_content": lambda s,a,f: import_identical(s,a,f,session=True),
        "session_import_changed_content": lambda s,a,f: import_changed(s,a,f,session=True),
        "sublayer_insert": sublayers,
        "sublayer_insert_restore": lambda s,a,f: sublayers(s,a,f,restore=True),
        "sublayer_reorder": sublayer_reorder,
        "session_sublayer_insert_restore": session_content,
        "mute_unmute": mute,
        "edit_target_restore": edit_target,
        "layer_identifier": layer_identifier,
        "stage_reassignment": lambda s,a,f: setattr(a, "stage", Usd.Stage.CreateInMemory()),
    }
    repo = Path(__file__).resolve().parents[2]
    source_paths = ["isaac/workcell/state.py", "isaac/workcell/cache_guard.py",
                    "isaac/workcell/build_usd.py", "isaac/workcell/layout.py",
                    "tests/isaac/usd_cache_tamper_check.py"]
    report = {"passed": False, "scope": "actual USD structure only", "cases": [],
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "bindings": missing_binding(base, None, None),
              "layout_sha256": hashlib.sha256(canonical_bytes(layout)).hexdigest(),
              "source_sha256": {name: hashlib.sha256((repo/name).read_bytes()).hexdigest()
                                for name in source_paths}}
    with tempfile.TemporaryDirectory(prefix="workcell-cache-tamper-") as directory:
        folder = Path(directory)
        for cached, name, mutate in ((cached, name, mutate) for cached in (False, True)
                                     for name, mutate in cases.items()):
            case_name = ("cached-" if cached else "uncached-") + name
            layer = Sdf.Layer.CreateNew(str(folder / (case_name + ".usda")))
            layer.ImportFromString(source)
            # Pre-existing empty sublayers let reordering/muting be tested
            # without an earlier insertion already latching the failure.
            children = [Sdf.Layer.CreateAnonymous(), Sdf.Layer.CreateAnonymous()]
            layer.subLayerPaths = [child.identifier for child in children]
            layer.Save()
            stage = Usd.Stage.Open(layer)
            access = None
            noop = name.endswith("import_same_content")
            result = {"name": name, "handle_cache_enabled": cached, "passed": False,
                      "classification": "verified_noop" if noop else "mutation_rejection"}
            try:
                access = StateAccessors(stage, layout, enable_handle_cache=cached)
                assert access.read_state() == baseline
                access.read_public_state()
                detail = mutate(stage, access, folder)
                if noop:
                    result.update(passed=detail["passed"], noop_evidence=detail)
                    continue
                errors = {}
                for label, read in (("full", access.read_state), ("public", access.read_public_state),
                                    ("environment", access.read_environment), ("write", lambda: access.apply_subset({}))):
                    try:
                        read()
                    except Exception as error:
                        errors[label] = type(error).__name__ + ": " + str(error)
                    else:
                        errors[label] = None
                result.update(passed=all(errors.values()), read_rejections=errors)
            except Exception as error:
                result["setup_or_mutation_error"] = type(error).__name__ + ": " + str(error)
            finally:
                if access is not None:
                    access.close()
                report["cases"].append(result)
    report["passed"] = all(case["passed"] for case in report["cases"] if case["handle_cache_enabled"])
    report["uncached_comparison"] = {
        "scope": "new uncached reader; not the original frozen baseline",
        "rejected_mutation_cases": sum(case["passed"] for case in report["cases"]
                                       if not case["handle_cache_enabled"] and case["classification"] == "mutation_rejection"),
        "verified_noop_cases": sum(case["passed"] for case in report["cases"]
                                   if not case["handle_cache_enabled"] and case["classification"] == "verified_noop"),
        "nonrejecting_or_failed_cases": [case["name"] for case in report["cases"]
                                        if not case["handle_cache_enabled"] and not case["passed"]],
        "qualification_claim": False,
    }
    report["required_cached_mutation_cases"] = len(cases) - 2
    report["required_cached_noop_cases"] = 2
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    with output.open("x") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases": len(report["cases"]), "output": str(output)}))
    if not report["passed"]:
        raise AssertionError("Handle cache remains unqualified; inspect failed tamper cases")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)

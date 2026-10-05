"""Validated USD-backed state for scripted, kinematic workcell props.

Authored velocity attributes describe scripted state, not a PhysX rigid-body
velocity measurement. Articulation state is read independently by its adapter.
"""
from copy import deepcopy
import math

FIELDS = {"position_m", "rotation_xyzw", "visible", "enabled", "collision_enabled",
          "linear_velocity_m_s", "angular_velocity_rad_s", "state"}


def validate_states(layout, states, *, public_only=False):
    definitions = {item["id"]: item for item in layout["objects"]}
    if not isinstance(states, dict) or set(states) != set(definitions):
        raise ValueError("Exact semantic object registry required")
    for identifier, item in states.items():
        expected = FIELDS - {"collision_enabled", "linear_velocity_m_s", "angular_velocity_rad_s"} if public_only else FIELDS
        if set(item) != expected:
            raise ValueError("State fields differ: " + identifier)
        vectors = [("position_m", 3), ("rotation_xyzw", 4)]
        if not public_only: vectors += [("linear_velocity_m_s", 3), ("angular_velocity_rad_s", 3)]
        for key, length in vectors:
            value = item[key]
            if not isinstance(value, (list, tuple)) or len(value) != length or not all(
                    type(v) in (int, float) and math.isfinite(v) for v in value):
                raise ValueError("Invalid finite vector: " + key)
        if abs(sum(v*v for v in item["rotation_xyzw"]) - 1) > 1e-5:
            raise ValueError("Unit quaternion required")
        for key in (("visible", "enabled") if public_only else ("visible", "enabled", "collision_enabled")):
            if type(item[key]) is not bool:
                raise ValueError("Boolean required: " + key)
        neutral = definitions[identifier]["state"]
        if not isinstance(item["state"], dict) or set(item["state"]) != set(neutral):
            raise ValueError("Object visual state keyset differs")
        for key, value in item["state"].items():
            if key == "location":
                if type(value) is not str or value not in layout["anchor_ids"]:
                    raise ValueError("Unregistered physical anchor")
            elif key == "tag_attached":
                if type(value) is not bool: raise ValueError("Boolean tag state required")
            elif key == "card_face":
                if type(value) is not int or value not in (0, 1): raise ValueError("Card face must be0/1")
            elif key in ("arrow_angle_rad", "lid_open_fraction"):
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Finite numeric visual state required")
                if key == "lid_open_fraction" and not 0 <= value <= 1:
                    raise ValueError("Lid fraction outside0..1")
            else:
                raise ValueError("Unsupported visual state field")
    return deepcopy(states)


class StateAccessors:
    def __init__(self, stage, layout):
        self.stage, self.layout = stage, layout
        self.anchor_ids = tuple(layout["anchor_ids"])
        self.definitions = {item["id"]: item for item in layout["objects"]}
        from pxr import UsdPhysics
        self._collision_attributes = {
            identifier: [UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr() for prim in
                         self._collision_prims(stage.GetPrimAtPath(definition["prim_path"]))]
            for identifier, definition in self.definitions.items()}
        self._structure_changed = False
        self._allowed_changes = set()
        for identifier, definition in self.definitions.items():
            path=definition["prim_path"]
            names=["xformOp:translate","xformOp:orient","visibility","workcell:enabled",
                   "workcell:linearVelocity","workcell:angularVelocity"]
            names += ["workcell:"+key for key in definition["state"]]
            self._allowed_changes.update(path+"."+name for name in names)
            for key,operation in (("card_face","rotateX"),("arrow_angle_rad","rotateZ"),("lid_open_fraction","rotateY")):
                if key in definition["state"]: self._allowed_changes.add(path+"/Visual.xformOp:"+operation)
            self._allowed_changes.update(str(attribute.GetPath()) for attribute in self._collision_attributes[identifier])
        for name in layout["materials"]:
            self._allowed_changes.update("/World/Workcell/Materials/"+name+"/Shader.inputs:"+key for key in ("diffuseColor","roughness"))
        for light in layout["lights"]:
            self._allowed_changes.update("/World/Workcell/Lights/"+light["id"]+".inputs:"+key for key in ("color","intensity"))
        from pxr import Tf, Usd
        self._notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._on_stage_changed, stage)

    def _on_stage_changed(self, notice, sender):
        # Cached geometry handles remain safe only while topology is unchanged.
        if any(path.IsPrimPath() and str(path).startswith("/World/Workcell") for path in notice.GetResyncedPaths()):
            self._structure_changed = True
        for path in notice.GetChangedInfoOnlyPaths():
            value=str(path)
            if value.startswith("/World/Workcell") and value not in self._allowed_changes:
                self._structure_changed = True

    def read_public_state(self):
        from pxr import UsdGeom, UsdPhysics
        for parent in ("/World", "/World/Workcell", "/World/Workcell/Objects"):
            prim = self.stage.GetPrimAtPath(parent)
            if prim and prim.IsA(UsdGeom.Xformable) and UsdGeom.Xformable(prim).GetOrderedXformOps():
                raise ValueError("Workcell ancestry must have identity transforms")
            if prim and prim.IsA(UsdGeom.Imageable) and UsdGeom.Imageable(prim).GetVisibilityAttr().Get() == UsdGeom.Tokens.invisible:
                raise ValueError("Workcell ancestor unexpectedly hidden")
        if self._structure_changed: raise ValueError("Workcell topology changed after registry capture")
        result = {}
        for identifier, definition in self.definitions.items():
            prim = self.stage.GetPrimAtPath(definition["prim_path"])
            if not prim: raise ValueError("Missing semantic prim: " + identifier)
            if [str(op.GetOpName()) for op in UsdGeom.Xformable(prim).GetOrderedXformOps()] != ["xformOp:translate", "xformOp:orient"]:
                raise ValueError("Unexpected semantic transform operations")
            q = prim.GetAttribute("xformOp:orient").Get()
            result[identifier] = dict(
                position_m=list(prim.GetAttribute("xformOp:translate").Get()),
                rotation_xyzw=[*q.GetImaginary(), q.GetReal()],
                visible=UsdGeom.Imageable(prim).GetVisibilityAttr().Get() != UsdGeom.Tokens.invisible,
                enabled=prim.GetAttribute("workcell:enabled").Get(),
                state={key: prim.GetAttribute("workcell:" + key).Get() for key in definition["state"]})
            state = result[identifier]["state"]
            visual = self.stage.GetPrimAtPath(str(prim.GetPath()) + "/Visual")
            for key, operation, expected in (
                ("card_face", "xformOp:rotateX", lambda v: 180. * v),
                ("arrow_angle_rad", "xformOp:rotateZ", math.degrees),
                ("lid_open_fraction", "xformOp:rotateY", lambda v: math.degrees(definition["open_angle_rad"]) * v)):
                if key in state and abs(visual.GetAttribute(operation).Get() - expected(state[key])) > 2e-5:
                    raise ValueError("Visual geometry disagrees with semantic state: " + identifier)
        return validate_states(self.layout, result, public_only=True)

    def read_state(self):
        from pxr import UsdGeom, UsdPhysics
        for parent in ("/World", "/World/Workcell", "/World/Workcell/Objects"):
            prim = self.stage.GetPrimAtPath(parent)
            if prim and prim.IsA(UsdGeom.Xformable) and UsdGeom.Xformable(prim).GetOrderedXformOps():
                raise ValueError("Workcell ancestry must have identity transforms")
            if prim and prim.IsA(UsdGeom.Imageable) and UsdGeom.Imageable(prim).GetVisibilityAttr().Get() == UsdGeom.Tokens.invisible:
                raise ValueError("Workcell ancestor unexpectedly hidden")
        if self._structure_changed: raise ValueError("Workcell topology changed after registry capture")
        result = {}
        for identifier, definition in self.definitions.items():
            prim = self.stage.GetPrimAtPath(definition["prim_path"])
            if not prim: raise ValueError("Missing semantic prim: " + identifier)
            if [str(op.GetOpName()) for op in UsdGeom.Xformable(prim).GetOrderedXformOps()] != ["xformOp:translate", "xformOp:orient"]:
                raise ValueError("Unexpected semantic transform operations")
            q = prim.GetAttribute("xformOp:orient").Get()
            collision = [bool(attribute.Get()) for attribute in self._collision_attributes[identifier]]
            if not collision or len(set(collision)) != 1:
                raise ValueError("Collision state must be uniformly authored")
            result[identifier] = dict(
                position_m=list(prim.GetAttribute("xformOp:translate").Get()),
                rotation_xyzw=[*q.GetImaginary(), q.GetReal()],
                visible=UsdGeom.Imageable(prim).GetVisibilityAttr().Get() != UsdGeom.Tokens.invisible,
                enabled=prim.GetAttribute("workcell:enabled").Get(), collision_enabled=collision[0],
                linear_velocity_m_s=list(prim.GetAttribute("workcell:linearVelocity").Get()),
                angular_velocity_rad_s=list(prim.GetAttribute("workcell:angularVelocity").Get()),
                state={key: prim.GetAttribute("workcell:" + key).Get() for key in definition["state"]})
            state = result[identifier]["state"]
            visual = self.stage.GetPrimAtPath(str(prim.GetPath()) + "/Visual")
            for key, operation, expected in (
                ("card_face", "xformOp:rotateX", lambda v: 180. * v),
                ("arrow_angle_rad", "xformOp:rotateZ", math.degrees),
                ("lid_open_fraction", "xformOp:rotateY", lambda v: math.degrees(definition["open_angle_rad"]) * v)):
                if key in state and abs(visual.GetAttribute(operation).Get() - expected(state[key])) > 2e-5:
                    raise ValueError("Visual geometry disagrees with semantic state: " + identifier)
        return validate_states(self.layout, result)

    @staticmethod
    def _collision_prims(root):
        from pxr import Usd, UsdGeom
        return (prim for prim in Usd.PrimRange(root) if prim.IsA(UsdGeom.Gprim))

    def apply_state(self, states):
        from pxr import Gf, UsdGeom, UsdPhysics
        states = validate_states(self.layout, states)  # Entire payload before mutation.
        for identifier, item in states.items():
            prim = self.stage.GetPrimAtPath(self.definitions[identifier]["prim_path"])
            prim.GetAttribute("xformOp:translate").Set(Gf.Vec3d(*item["position_m"]))
            q = item["rotation_xyzw"]
            prim.GetAttribute("xformOp:orient").Set(Gf.Quatd(q[3], Gf.Vec3d(*q[:3])))
            UsdGeom.Imageable(prim).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited if item["visible"] else UsdGeom.Tokens.invisible)
            prim.GetAttribute("workcell:enabled").Set(item["enabled"])
            prim.GetAttribute("workcell:linearVelocity").Set(Gf.Vec3d(*item["linear_velocity_m_s"]))
            prim.GetAttribute("workcell:angularVelocity").Set(Gf.Vec3d(*item["angular_velocity_rad_s"]))
            for attribute in self._collision_attributes[identifier]:
                attribute.Set(item["collision_enabled"])
            for key, value in item["state"].items():
                prim.GetAttribute("workcell:" + key).Set(value)
            self._apply_visual_state(prim, item["state"], self.definitions[identifier])

    def _apply_visual_state(self, prim, state, definition):
        visual = self.stage.GetPrimAtPath(str(prim.GetPath()) + "/Visual")
        if "card_face" in state:
            visual.GetAttribute("xformOp:rotateX").Set(180. * state["card_face"])
        if "arrow_angle_rad" in state:
            visual.GetAttribute("xformOp:rotateZ").Set(math.degrees(state["arrow_angle_rad"]))
        if "lid_open_fraction" in state:
            visual.GetAttribute("xformOp:rotateY").Set(math.degrees(definition["open_angle_rad"]) * state["lid_open_fraction"])

    def read_environment(self):
        result = {"materials": {}, "lights": {}}
        for name in self.layout["materials"]:
            prim = self.stage.GetPrimAtPath("/World/Workcell/Materials/" + name + "/Shader")
            result["materials"][name] = {"diffuse_color": list(prim.GetAttribute("inputs:diffuseColor").Get()),
                                        "roughness": prim.GetAttribute("inputs:roughness").Get()}
        for light in self.layout["lights"]:
            prim = self.stage.GetPrimAtPath("/World/Workcell/Lights/" + light["id"])
            result["lights"][light["id"]] = {"color": list(prim.GetAttribute("inputs:color").Get()),
                                            "intensity": prim.GetAttribute("inputs:intensity").Get()}
        return result

    def apply_environment(self, environment):
        from pxr import Gf
        expected = self.read_environment()
        if set(environment) != set(expected): raise ValueError("Environment domains differ")
        for group in expected:
            if set(environment[group]) != set(expected[group]): raise ValueError("Environment registry differs")
            for identifier, fields in expected[group].items():
                value = environment[group][identifier]
                if set(value) != set(fields): raise ValueError("Environment fields differ")
                for key, reference in fields.items():
                    item = value[key]
                    values = item if isinstance(reference, list) else [item]
                    if isinstance(reference, list) and (not isinstance(item, list) or len(item) != len(reference)):
                        raise ValueError("Environment vector differs")
                    if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
                        raise ValueError("Environment must be finite")
        for name, value in environment["materials"].items():
            prim = self.stage.GetPrimAtPath("/World/Workcell/Materials/" + name + "/Shader")
            prim.GetAttribute("inputs:diffuseColor").Set(Gf.Vec3f(*value["diffuse_color"]))
            prim.GetAttribute("inputs:roughness").Set(value["roughness"])
        for name, value in environment["lights"].items():
            prim = self.stage.GetPrimAtPath("/World/Workcell/Lights/" + name)
            prim.GetAttribute("inputs:color").Set(Gf.Vec3f(*value["color"]))
            prim.GetAttribute("inputs:intensity").Set(value["intensity"])

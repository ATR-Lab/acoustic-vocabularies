"""Validated USD-backed state for scripted, kinematic workcell props.

Authored velocity attributes describe scripted state, not a PhysX rigid-body
velocity measurement. Articulation state is read independently by its adapter.
"""
from copy import deepcopy
import math

FIELDS = {"position_m", "rotation_xyzw", "visible", "enabled", "collision_enabled",
          "linear_velocity_m_s", "angular_velocity_rad_s", "state"}


def _validate_states(layout, states, *, public_only=False):
    """Validate in place without retaining or cloning the caller's values."""
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
    return states


def validate_states(layout, states, *, public_only=False):
    """Validate and detach caller-owned input before any state mutation."""
    return deepcopy(_validate_states(layout, states, public_only=public_only))


class StateAccessors:
    def __init__(self, stage, layout, *, enable_handle_cache=False):
        # Registry authority is detached once. Individual reads never retain
        # caller-owned output or mutable values from earlier samples.
        self.stage, self._origin_stage = stage, stage
        self.layout = layout = deepcopy(layout)
        self._cache_guard = None
        self._records = None
        self._ancestor_records = None
        self._closed = False
        self._structure_error = None
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
        if enable_handle_cache:
            self.enable_handle_cache()

    @property
    def handle_cache_enabled(self):
        return self._records is not None

    def _invalidate(self, reason):
        self._structure_changed = True
        if self._structure_error is None:
            self._structure_error = reason

    def _on_stage_changed(self, notice, sender):
        from .cache_guard import affects_workcell
        try:
            if any(affects_workcell(path) for path in notice.GetResyncedPaths()):
                self._invalidate("Workcell prim, property or ancestry was resynced")
            for path in notice.GetChangedInfoOnlyPaths():
                if not affects_workcell(path):
                    continue
                # A value edit is allowed; changing its type, connection,
                # variability or other metadata is a structural fault.
                if (str(path) not in self._allowed_changes or
                        set(notice.GetChangedFields(path)) != {"default"}):
                    self._invalidate("Unsupported workcell property or metadata change: " + str(path))
        except Exception:
            self._invalidate("USD change notice could not be validated")

    def enable_handle_cache(self):
        """Explicit experimental opt-in after construction; never implicit fallback.

        Required notice bindings and actual tamper tests must be qualified in the
        pinned runtime before a caller enables this in a performance run.
        """
        if self.handle_cache_enabled:
            raise ValueError("Handle cache already initialized")
        self._check_structure()
        from .cache_guard import HandleCacheGuard
        try:
            self._cache_guard = HandleCacheGuard(self.stage, self._invalidate)
            self._ancestor_records = self._get_ancestor_records()
            self._records = {identifier: self._get_record(definition)
                             for identifier, definition in self.definitions.items()}
            # Check all captured handles and the complete live state before use.
            self.read_state()
            self.read_environment()
        except Exception:
            if self._cache_guard is not None:
                self._cache_guard.close()
            self._cache_guard = self._records = self._ancestor_records = None
            self._invalidate("Handle cache initialization failed")
            raise

    def close(self):
        self._closed = True
        self._notice.Revoke()
        if self._cache_guard is not None:
            self._cache_guard.close()

    def _get_ancestor_records(self):
        from pxr import UsdGeom
        result = []
        for path in ("/World", "/World/Workcell", "/World/Workcell/Objects"):
            prim = self.stage.GetPrimAtPath(path)
            if not prim:
                raise ValueError("Missing workcell ancestor: " + path)
            # USD may create untyped namespace ancestors. They have no schema
            # attributes until authored, and that later creation is a resync.
            order = prim.GetAttribute("xformOpOrder") if prim.HasAttribute("xformOpOrder") else None
            visibility = prim.GetAttribute("visibility") if prim.HasAttribute("visibility") else None
            result.append((prim, order, visibility))
        return result

    def _check_structure(self):
        from pxr import UsdGeom
        if self._closed:
            raise ValueError("State accessor is closed")
        if self.stage is not self._origin_stage:
            self._invalidate("Accessor stage identity changed")
        if self._cache_guard is not None:
            try:
                self._cache_guard.check()
            except Exception:
                self._invalidate("USD layer identity check failed")
        if self._structure_changed:
            raise ValueError(self._structure_error or "Workcell topology changed after registry capture")
        records = self._ancestor_records or self._get_ancestor_records()
        for prim, order, visibility in records:
            if not prim or (order is not None and not order) or (visibility is not None and not visibility):
                self._invalidate("Workcell ancestor handle invalid")
                raise ValueError(self._structure_error)
            if order is not None and order.Get():
                self._invalidate("Workcell ancestry must have identity transforms")
                raise ValueError(self._structure_error)
            if visibility is not None and visibility.Get() == UsdGeom.Tokens.invisible:
                self._invalidate("Workcell ancestor unexpectedly hidden")
                raise ValueError(self._structure_error)

    def _get_record(self, definition):
        from pxr import UsdGeom
        prim = self.stage.GetPrimAtPath(definition["prim_path"])
        if not prim:
            raise ValueError("Missing semantic prim: " + definition["id"])
        order = UsdGeom.Xformable(prim).GetXformOpOrderAttr()
        if list(order.Get() or []) != ["xformOp:translate", "xformOp:orient"]:
            raise ValueError("Unexpected semantic transform operations")
        names = ("xformOp:translate", "xformOp:orient", "visibility", "workcell:enabled",
                 "workcell:linearVelocity", "workcell:angularVelocity")
        attributes = {name: prim.GetAttribute(name) for name in names}
        state = {key: prim.GetAttribute("workcell:" + key) for key in definition["state"]}
        visual = self.stage.GetPrimAtPath(definition["prim_path"] + "/Visual")
        visual_attributes = []
        for key, operation, factor in (
            ("card_face", "xformOp:rotateX", 180.),
            ("arrow_angle_rad", "xformOp:rotateZ", 180. / math.pi),
            ("lid_open_fraction", "xformOp:rotateY", math.degrees(definition.get("open_angle_rad", 0.)))):
            if key in state:
                visual_attributes.append((key, visual.GetAttribute(operation), factor))
        handles = (prim, order, *attributes.values(), *state.values(),
                   *(item[1] for item in visual_attributes),
                   *self._collision_attributes[definition["id"]])
        if not all(handles):
            raise ValueError("Missing semantic attribute or geometry handle")
        return attributes, state, visual_attributes, handles

    def _read_objects(self, *, public_only):
        from pxr import UsdGeom
        self._check_structure()
        result = {}
        for identifier, definition in self.definitions.items():
            record = self._records[identifier] if self._records is not None else self._get_record(definition)
            attributes, state_attributes, visual_attributes, handles = record
            if not all(handles):
                self._invalidate("Semantic handle invalid: " + identifier)
                raise ValueError(self._structure_error)
            if list(handles[1].Get() or []) != ["xformOp:translate", "xformOp:orient"]:
                self._invalidate("Semantic transform operation order changed")
                raise ValueError(self._structure_error)
            # Every sample obtains values from USD. Handles, never values or
            # validation results, are reused by the optional cache.
            q = attributes["xformOp:orient"].Get()
            visibility = attributes["visibility"].Get()
            if visibility not in (UsdGeom.Tokens.inherited, UsdGeom.Tokens.invisible):
                raise ValueError("Invalid semantic visibility token: " + identifier)
            item = dict(
                position_m=list(attributes["xformOp:translate"].Get()),
                rotation_xyzw=[*q.GetImaginary(), q.GetReal()],
                visible=visibility != UsdGeom.Tokens.invisible,
                enabled=attributes["workcell:enabled"].Get(),
                state={key: attribute.Get() for key, attribute in state_attributes.items()})
            if not public_only:
                collision = [attribute.Get() for attribute in self._collision_attributes[identifier]]
                if not collision or any(type(value) is not bool for value in collision) or len(set(collision)) != 1:
                    raise ValueError("Collision state must be uniformly authored booleans")
                item.update(collision_enabled=collision[0],
                            linear_velocity_m_s=list(attributes["workcell:linearVelocity"].Get()),
                            angular_velocity_rad_s=list(attributes["workcell:angularVelocity"].Get()))
            for key, attribute, factor in visual_attributes:
                visual_value = attribute.Get()
                if (type(visual_value) not in (int, float) or not math.isfinite(visual_value) or
                        abs(visual_value - factor * item["state"][key]) > 2e-5):
                    raise ValueError("Visual geometry disagrees with semantic state: " + identifier)
            result[identifier] = item
        # All containers above are new and all leaf values are schema primitives.
        # The public validator still detaches external mutation inputs.
        return _validate_states(self.layout, result, public_only=public_only)

    def read_public_state(self):
        return self._read_objects(public_only=True)

    def read_state(self):
        return self._read_objects(public_only=False)

    @staticmethod
    def _collision_prims(root):
        from pxr import Usd, UsdGeom
        return (prim for prim in Usd.PrimRange(root) if prim.IsA(UsdGeom.Gprim))

    def apply_state(self, states):
        self._apply_validated(validate_states(self.layout, states))

    def apply_subset(self, states):
        """Atomically validate a selected semantic registry before any USD writes.

        Values are complete per-object state records; partial field patches and
        unknown IDs are rejected. Used for small carried groups in scripted motion.
        """
        if not isinstance(states, dict) or set(states) - set(self.definitions):
            raise ValueError("Unknown semantic object subset")
        selected = {**self.layout, "objects": [self.definitions[key] for key in states]}
        self._apply_validated(validate_states(selected, states))

    def _apply_validated(self, states):
        from pxr import Gf, UsdGeom, UsdPhysics
        self._check_structure()
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
        self._check_structure()
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

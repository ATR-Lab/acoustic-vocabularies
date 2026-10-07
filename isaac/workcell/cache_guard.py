"""Opt-in USD handle-cache lifetime guard; no state values are cached here.

Enable only after scene construction. A guard fault is permanent; reconstruct
and verify the scene rather than silently rebuilding handles on changed data.
"""


def affects_workcell(path):
    """Include properties, descendants, root and ancestors, not prefix siblings."""
    prim_path = str(path).split(".", 1)[0]
    root = "/World/Workcell"
    return (prim_path == "/" or prim_path == root or
            prim_path.startswith(root + "/") or root.startswith(prim_path + "/"))


class HandleCacheGuard:
    """Require notice bindings and independently compare live layer identities."""

    def __init__(self, stage, invalidate):
        from pxr import Sdf, Tf, Usd
        self.stage, self.invalidate = stage, invalidate
        self._notices = []
        required = (
            (Usd.Notice, "StageEditTargetChanged"),
            (Usd.Notice, "LayerMutingChanged"),
            (Sdf.Notice, "LayerDidReplaceContent"),
            (Sdf.Notice, "LayerDidReloadContent"),
            (Sdf.Notice, "LayerIdentifierDidChange"),
            (Sdf.Notice, "LayerInfoDidChange"),
            (Sdf.Notice, "LayerMutenessChanged"),
        )
        missing = [name for owner, name in required if not hasattr(owner, name)]
        if not hasattr(Usd.Notice.ObjectsChanged, "GetChangedFields"):
            missing.append("ObjectsChanged.GetChangedFields")
        if not hasattr(Tf.Notice, "RegisterGlobally"):
            missing.append("Tf.Notice.RegisterGlobally")
        muting = getattr(Sdf.Notice, "LayerMutenessChanged", None)
        if muting is not None and not hasattr(muting, "layerPath"):
            missing.append("LayerMutenessChanged.layerPath")
        if missing:
            raise RuntimeError("Handle cache unavailable; missing USD bindings: " + ", ".join(missing))
        # Include every layer contributing to authored workcell prims, as well
        # as the ordered root/session stack. Robot asset layers are outside this
        # workcell guard and remain the reset adapter's separate responsibility.
        layers = set(stage.GetLayerStack(True))
        for prim in Usd.PrimRange(stage.GetPrimAtPath("/World/Workcell")):
            layers.update(spec.layer for spec in prim.GetPrimStack())
        self._layers = tuple(sorted(layers, key=lambda layer: layer.identifier))
        self._signature = self._read_signature()
        try:
            for notice_type in (Usd.Notice.StageEditTargetChanged, Usd.Notice.LayerMutingChanged):
                self._notices.append(Tf.Notice.Register(notice_type, self._on_change, stage))
            self._notices.append(Tf.Notice.RegisterGlobally(
                Sdf.Notice.LayerMutenessChanged, self._on_muteness))
            for layer in self._layers:
                for notice_type in (Sdf.Notice.LayerDidReplaceContent,
                                    Sdf.Notice.LayerDidReloadContent,
                                    Sdf.Notice.LayerIdentifierDidChange,
                                    Sdf.Notice.LayerInfoDidChange):
                    self._notices.append(Tf.Notice.Register(notice_type, self._on_change, layer))
        except Exception:
            self.close()
            raise

    def _read_signature(self):
        return (
            self.stage.GetRootLayer(), self.stage.GetSessionLayer(),
            tuple(self.stage.GetLayerStack(True)),
            tuple(self.stage.GetMutedLayers()), self.stage.GetEditTarget(),
            tuple((layer, layer.identifier, layer.IsMuted(), tuple(layer.subLayerPaths),
                   tuple((offset.offset, offset.scale) for offset in layer.subLayerOffsets))
                  for layer in self._layers),
        )

    def _on_change(self, notice, sender):
        self.invalidate("USD layer content, identity, composition or edit target changed")

    def _on_muteness(self, notice, sender):
        if notice.layerPath in {layer.identifier for layer in self._layers}:
            self._on_change(notice, sender)

    def check(self):
        if self._read_signature() != self._signature:
            self.invalidate("USD contributing layer stack changed")

    def close(self):
        for notice in self._notices:
            notice.Revoke()
        self._notices.clear()

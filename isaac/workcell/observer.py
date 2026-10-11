"""Observer reference camera for the generated workcell scene.

The observer camera prim is part of the exported scene, so it is part of the pinned
scene hash. `--capture` creates it through an Isaac Lab `Camera` sensor, which also
needs the RTX renderer. `--observer-prim-only` spawns the same USD prim without a
sensor, render product or renderer, for hosts whose driver cannot start RTX. That
mode produces no evidence images and supports physics/state services only.
"""
OBSERVER_PRIM_PATH = '/World/ObserverReference'


def observer_mode(capture, observer_prim_only):
    """Return 'camera', 'prim_only' or 'none'; the two flags are mutually exclusive."""
    if capture and observer_prim_only:
        raise ValueError('--capture and --observer-prim-only are mutually exclusive')
    return 'camera' if capture else 'prim_only' if observer_prim_only else 'none'


def observer_camera_cfg(observer, sim_utils, camera_cfg):
    """The single observer camera configuration used by both modes."""
    return camera_cfg(prim_path=OBSERVER_PRIM_PATH, update_period=0.,
        width=observer['width'], height=observer['height'], data_types=['rgb'],
        spawn=sim_utils.PinholeCameraCfg(focal_length=observer['focal_length_mm'],
            horizontal_aperture=observer['horizontal_aperture_mm'], clipping_range=(.01, 100.)))


def spawn_observer_prim(cfg, torch, convert_orientation):
    """Render-free equivalent of the USD-authoring part of `isaaclab.sensors.Camera`.

    It follows the pinned Isaac Lab 2.3.2 constructor exactly:
    - `SensorBase.__init__`: validate, then work on a copy.
    - `Camera.__init__`: convert the offset rotation from its convention to OpenGL,
      default the vertical aperture for square pixels, and spawn.
    It does not register sensor callbacks, set `/isaaclab/render/rtx_sensors`, or
    create a render product. Returns the spawned (copied) configuration.
    """
    cfg.validate()
    cfg = cfg.copy()
    rot = torch.tensor(cfg.offset.rot, dtype=torch.float32, device='cpu').unsqueeze(0)
    rot_offset = convert_orientation(rot, origin=cfg.offset.convention, target='opengl')
    rot_offset = rot_offset.squeeze(0).cpu().numpy()
    if cfg.spawn.vertical_aperture is None:
        cfg.spawn.vertical_aperture = cfg.spawn.horizontal_aperture * cfg.height / cfg.width
    cfg.spawn.func(cfg.prim_path, cfg.spawn, translation=cfg.offset.pos, orientation=rot_offset)
    return cfg

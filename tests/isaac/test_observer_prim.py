"""Observer camera prim parity between --capture and --observer-prim-only (no Isaac required).

The scene-hash equality itself needs Isaac; it is recorded natively in
docs/isaac/e2e/2026-10-11-isaac2-host.json. These tests pin the shared
configuration and the Camera-constructor steps the render-free path reproduces.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from isaac.workcell.layout import neutral_layout
from isaac.workcell.observer import OBSERVER_PRIM_PATH, observer_camera_cfg, observer_mode, spawn_observer_prim

RUN_SCENE = Path(__file__).resolve().parents[2] / 'isaac/workcell/run_scene.py'


class Recorder:
    def __init__(self, **kwargs): self.kwargs = kwargs


def test_mode_flags_are_exclusive():
    assert observer_mode(True, False) == 'camera'
    assert observer_mode(False, True) == 'prim_only'
    assert observer_mode(False, False) == 'none'
    with pytest.raises(ValueError, match='mutually exclusive'):
        observer_mode(True, True)


def test_single_observer_configuration_from_the_hashed_layout():
    observer = neutral_layout()['observer']
    sim_utils = SimpleNamespace(PinholeCameraCfg=Recorder)
    cfg = observer_camera_cfg(observer, sim_utils, Recorder)
    assert cfg.kwargs['prim_path'] == OBSERVER_PRIM_PATH == '/World/ObserverReference'
    assert cfg.kwargs['update_period'] == 0. and cfg.kwargs['data_types'] == ['rgb']
    assert (cfg.kwargs['width'], cfg.kwargs['height']) == (observer['width'], observer['height'])
    assert cfg.kwargs['spawn'].kwargs == dict(focal_length=observer['focal_length_mm'],
        horizontal_aperture=observer['horizontal_aperture_mm'], clipping_range=(.01, 100.))


class FakeTensor:
    def __init__(self, value, log): self.value, self.log = value, log
    def unsqueeze(self, dim): self.log.append(('unsqueeze', dim)); return self
    def squeeze(self, dim): self.log.append(('squeeze', dim)); return self
    def cpu(self): return self
    def numpy(self): return ('opengl', self.value)


def fake_cfg(log, vertical=None):
    spawn = SimpleNamespace(horizontal_aperture=20.955, vertical_aperture=vertical,
                            func=lambda path, spawn, **kw: log.append(('spawn', path, spawn.vertical_aperture, kw)))
    cfg = SimpleNamespace(prim_path=OBSERVER_PRIM_PATH, width=1280, height=720, spawn=spawn,
                          offset=SimpleNamespace(pos=(0., 0., 0.), rot=(1., 0., 0., 0.), convention='ros'))
    def copy():
        log.append(('copy',))
        return SimpleNamespace(**{**vars(cfg), 'spawn': SimpleNamespace(**vars(spawn))})
    cfg.validate = lambda: log.append(('validate',)); cfg.copy = copy
    return cfg


def test_render_free_spawn_follows_camera_constructor_steps():
    log = []
    torch = SimpleNamespace(float32='f32', tensor=lambda v, dtype, device: (log.append(('tensor', tuple(v), dtype, device)), FakeTensor(v, log))[1])
    def convert(rot, origin, target):
        log.append(('convert', origin, target)); return rot
    original = fake_cfg(log)
    spawned = spawn_observer_prim(original, torch, convert)
    assert [entry[0] for entry in log] == ['validate', 'copy', 'tensor', 'unsqueeze', 'convert', 'squeeze', 'spawn']
    assert ('convert', 'ros', 'opengl') in log
    _, path, vertical, kwargs = log[-1]
    assert path == OBSERVER_PRIM_PATH
    assert vertical == pytest.approx(20.955 * 720 / 1280)       # square-pixel default, as Camera.__init__
    assert kwargs == dict(translation=(0., 0., 0.), orientation=('opengl', (1., 0., 0., 0.)))
    assert original.spawn.vertical_aperture is None and spawned is not original   # caller's cfg is untouched


def test_explicit_vertical_aperture_is_kept():
    log = []
    torch = SimpleNamespace(float32='f32', tensor=lambda v, dtype, device: FakeTensor(v, log))
    spawn_observer_prim(fake_cfg(log, vertical=12.), torch, lambda rot, origin, target: rot)
    assert log[-1][2] == 12.


def test_run_scene_uses_the_shared_configuration_in_both_modes():
    tree = ast.parse(RUN_SCENE.read_text(encoding='utf-8'))
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert calls.count('observer_camera_cfg') == 2 and calls.count('spawn_observer_prim') == 1
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert '/World/ObserverReference' not in literals   # one definition, in isaac.workcell.observer

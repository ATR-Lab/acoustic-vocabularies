"""Pinned USD notice/proof checks; articulation values here are synthetic inputs.

Run in the existing approved CPU-only --network none image. This checks actual
USD authoring/notices and restored-state invalidation, not Isaac throughput.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pxr import Gf, Sdf, Usd
from isaac.e2e.same_iteration import SameIterationCapture, StageMutationWatch
from isaac.reset.manager import ResetManager
from isaac.reset.snapshot import capture, sha256
from isaac.workcell.build_usd import build_workcell
from isaac.workcell.layout import neutral_layout


class StructuralAdapter:
    scene_sha256 = "a"*64
    sim_time = 0.
    def __init__(self, access):
        self.accessors = access
        self.fixture = json.loads((ROOT/'isaac/snapshots/neutral_v1.json').read_text())['state']

    def read_state(self):
        return dict(robot=deepcopy(self.fixture['robot']), frames=deepcopy(self.fixture['frames']),
                    objects=self.accessors.read_state(), environment=self.accessors.read_environment())


def attribute_restore(stage, path, name, changed):
    attribute = stage.GetPrimAtPath(path).GetAttribute(name)
    previous = attribute.Get(); attribute.Set(changed); attribute.Set(previous)


def layer_insert_restore(stage):
    layer = Sdf.Layer.CreateAnonymous()
    previous = list(stage.GetRootLayer().subLayerPaths)
    stage.GetRootLayer().subLayerPaths = previous + [layer.identifier]
    stage.GetRootLayer().subLayerPaths = previous


def edit_target_restore(stage):
    previous = stage.GetEditTarget()
    stage.SetEditTarget(stage.GetSessionLayer()); stage.SetEditTarget(previous)


def import_restore(stage):
    layer = stage.GetRootLayer(); original = layer.ExportToString()
    replacement = Sdf.Layer.CreateAnonymous(); replacement.ImportFromString(original)
    replacement.customLayerData = {"engineering_mutation": 1}
    assert layer.ImportFromString(replacement.ExportToString())
    assert layer.ImportFromString(original)


def run_case(name, mutate, directory, *, noop=False):
    path = directory/(name+'.usda')
    stage = Usd.Stage.CreateNew(str(path))
    access = build_workcell(stage, neutral_layout())
    stage.GetRootLayer().Save()
    adapter = StructuralAdapter(access)
    snapshot = capture(adapter)
    manager = ResetManager(adapter, snapshot, sha256(snapshot), lambda _: None)
    watcher = None
    proof = SameIterationCapture(manager, lambda: watcher.identity())
    try:
        watcher = StageMutationWatch(adapter, proof.notify_mutation)
        proof.bind_iteration(1, adapter.sim_time)
        assert manager.verify_current(capture=proof)['reset_ok']
        before = proof._capture.state_bytes
        mutate(stage)
        try:
            _,_,value = proof.sample()
            accepted = proof.neutral_check(value)
        except (RuntimeError, ValueError):
            accepted = False
        row = dict(case=name, accepted_after_mutation=accepted, expected_noop=noop,
            mutation_generation=proof._generation, before_state_sha256=hashlib.sha256(before).hexdigest())
        if noop:
            row['live_state_identical'] = adapter.read_state() == snapshot['state']
            row['passed'] = row['live_state_identical'] and accepted
        else:
            row['passed'] = accepted is False
        return row
    finally:
        if watcher is not None: watcher.close()
        proof.close(); access.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError('Fresh output required')
    object_path='/World/Workcell/Objects/tray_A'
    cases=[
        ('object_value_restore',lambda stage:attribute_restore(stage,object_path,'workcell:enabled',False)),
        ('object_pose_restore',lambda stage:attribute_restore(stage,object_path,'xformOp:translate',Gf.Vec3d(.9,.8,.7))),
        ('visual_restore',lambda stage:attribute_restore(stage,'/World/Workcell/Objects/tray_A__card/Visual','xformOp:rotateX',180.)),
        ('environment_restore',lambda stage:attribute_restore(stage,'/World/Workcell/Lights/fixed_dome','inputs:intensity',123.)),
        ('layer_insert_restore',layer_insert_restore),
        ('edit_target_restore',edit_target_restore),
        ('content_import_restore',import_restore),
        ('forced_same_content_reload',lambda stage:stage.GetRootLayer().Reload(True)),
    ]
    report=dict(scope='actual_usd_structural_synthetic_articulation',participant=False,
        qualification=False,usd_version=list(Usd.GetVersion()),cases=[],error=None)
    try:
        with tempfile.TemporaryDirectory(prefix='av-proof-usd-') as folder:
            for name,change in cases:
                row=run_case(name,change,Path(folder));report['cases'].append(row)
                if not row['passed']:raise RuntimeError('Mutation proof unexpectedly accepted: '+name)
            report['cases'].append(run_case('byte_identical_import',
                lambda stage:stage.GetRootLayer().ImportFromString(stage.GetRootLayer().ExportToString()),
                Path(folder),noop=True))
    except Exception as error:
        report['error']=type(error).__name__+': '+str(error)
    report['passed']=report['error'] is None and len(report['cases'])==9 and all(x['passed'] for x in report['cases'])
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())

"""Bounded synthetic Python-to-actual-C#-DLL mailbox interoperability probe."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "schedules/src"))
from ops.console.transport import Mailbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-dll', required=True, type=Path)
    parser.add_argument('--fresh-directory', required=True, type=Path)
    args = parser.parse_args()
    args.fresh_directory.mkdir(parents=True, exist_ok=False)
    folder = args.fresh_directory
    receipts = []
    def receipt_sink(value):
        with (folder / 'python-receipts.local.jsonl').open('ab') as stream:
            stream.write((json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode())
            stream.flush(); os.fsync(stream.fileno())
        receipts.append(value)
    with (folder / 'stdout.txt').open('wb') as out, (folder / 'stderr.txt').open('wb') as err:
        child = subprocess.Popen(['dotnet', str(args.probe_dll.resolve()), str(folder.resolve())], stdout=out, stderr=err,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic()+10
            while not (folder/'state.json').exists():
                if child.poll() is not None or time.monotonic()>deadline:
                    raise RuntimeError('Actual C# probe did not publish state; inspect retained stderr')
                time.sleep(.025)
            mailbox = Mailbox(folder, receipt_sink=receipt_sink)
            visit = SimpleNamespace(manifest_hash='a'*64, schedule_hash='b'*64, package_hash='c'*64)
            mailbox.bind(visit)
            mailbox.command('start')
            time.sleep(.3)  # Synthetic virtual clock is 5x; cue is now underway.
            mailbox.command('pause')
            deadline = time.monotonic()+5
            while mailbox.snapshot()['engine_state'] != 'paused':
                if time.monotonic()>deadline: raise RuntimeError('Real engine did not hold its next unplayed slot')
                time.sleep(.025)
            paused = mailbox.snapshot()
            assert paused['completed_counts'] == [1]
            mailbox.command('resume')
            mailbox.command('stop')
            child.wait(timeout=5)
            assert child.returncode == 0
            assert [row['command'] for row in receipts] == ['load','start','pause','resume','stop']
            final=json.loads((folder/'state.json').read_bytes())
            assert final['engine_state']=='stopped' and final['receipt']['status']=='accepted'
            journal=next(folder.glob('operator-*.local.jsonl'))
            rows=[json.loads(line) for line in journal.read_text().splitlines()]
            assert len(rows)==10 and all(rows[i]['record']['kind']==('request' if i%2==0 else 'result') for i in range(10))
            result={'qualification':'synthetic_actual_dll_interop_no_device_or_joined_scene','commands':['load','start','pause','resume','stop'],
                    'durable_python_receipts':len(receipts),'durable_csharp_records':len(rows),'paused_completed_counts':paused['completed_counts'],
                    'final_state':final['engine_state'],'child_exit':child.returncode,'csharp_journal_sha256':hashlib.sha256(journal.read_bytes()).hexdigest(),
                    'audio_device_used':False,'participant_scene_used':False}
            (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result,indent=2))
        finally:
            if child.poll() is None: child.terminate();child.wait(timeout=5)


if __name__=='__main__': main()


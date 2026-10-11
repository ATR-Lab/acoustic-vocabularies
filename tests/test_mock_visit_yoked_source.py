"""Yoked replay-source evidence (#246); synthetic fixtures only.

Dyad attempts 030/031: the emitter tagged the yoked run's staged copy of the
active member's sealed ledger (files/menu_replay_ledger/menus.local.jsonl) as a
menu_journal, and native.menu checked that active chain against the yoked role
and schedule -> MOCK_MENU_BINDING, aborting reconciliation.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.mock_visit import native
from tools.mock_visit.records import EvidenceError, sha, strict
from tools.write_mock_run_manifest import emit

H, A, Y = "a" * 64, "b" * 64, "c" * 64  # package, active schedule, yoked schedule
KEYS = ["profile", "K-a1"]


def binding(role, schedule):
    return dict(package_sha256=H, bank_sha256=H, allocation_sha256=H, schedule_sha256=schedule, unit_binding_sha256=H,
                review_sha256=H, visit="V1", role=role, menu_keys=list(KEYS))


def chain(role, schedule, seal):
    rows = [dict(record=dict(kind="header", format="av-menu-ledger/1", binding=binding(role, schedule),
                             created_utc="2026-10-11T00:00:00.000Z", clock_epoch="d" * 32))]
    rows += [dict(record=dict(kind="menu_start", menu_key=k, event_id=f"{i + 1:032x}")) for i, k in enumerate(KEYS)]
    if seal: rows.append(dict(record=seal))
    return rows


class ReplaySource(unittest.TestCase):
    def config(self, ledger_hash):
        return dict(files=dict(menu_replay_ledger=dict(path="files/menu_replay_ledger/menus.local.jsonl", sha256=ledger_hash),
                               yoked_active_schedule=dict(path="content/active.json", sha256=A)))

    def check(self, source=None, file_hash=Y, config=None, yoked=None):
        source = source or chain("active", A, dict(kind="sealed", menu_count=2))
        yoked = yoked if yoked is not None else [chain("yoked", Y, dict(kind="sealed_yoked", menu_count=2, active_ledger_sha256=Y, yoked_event_sha256=H))]
        return native.replay_source(source, file_hash, config or self.config(Y), H, yoked)

    def test_active_sealed_ledger_is_accepted_as_the_yoked_replay_source(self):
        self.assertEqual(self.check()["role"], "active")
        self.assertEqual(self.check(yoked=[chain("yoked", Y, None)])["role"], "active", "An unsealed yoked run still binds its source")

    def test_the_source_chain_is_not_this_runs_menu_journal(self):
        # The original failure: the active chain checked as a yoked journal.
        with self.assertRaisesRegex(EvidenceError, "MOCK_MENU_BINDING"):
            native.menu([chain("active", A, dict(kind="sealed", menu_count=2))], {}, {}, {}, "yoked", Y, H)

    def test_tampered_or_mismatched_sources_are_refused(self):
        cases = []
        cases.append(("MOCK_MENU_REPLAY_SOURCE_PIN", dict(file_hash="e" * 64)))
        cases.append(("MOCK_MENU_REPLAY_SOURCE_PIN", dict(config=dict(files={}))))
        yoked_role = chain("yoked", A, dict(kind="sealed", menu_count=2)); cases.append(("MOCK_MENU_REPLAY_SOURCE_BINDING", dict(source=yoked_role)))
        wrong_schedule = chain("active", Y, dict(kind="sealed", menu_count=2)); cases.append(("MOCK_MENU_REPLAY_SOURCE_BINDING", dict(source=wrong_schedule)))
        unsealed = chain("active", A, None); cases.append(("MOCK_MENU_REPLAY_SOURCE_UNSEALED", dict(source=unsealed)))
        short = chain("active", A, dict(kind="sealed", menu_count=2)); del short[2]; cases.append(("MOCK_MENU_REPLAY_SOURCE_UNSEALED", dict(source=short)))
        other_unit = chain("yoked", Y, None); other_unit[0]["record"]["binding"]["allocation_sha256"] = "f" * 64
        cases.append(("MOCK_YOKED_UNIT_BINDING", dict(yoked=[other_unit])))
        wrong_seal = chain("yoked", Y, dict(kind="sealed_yoked", menu_count=2, active_ledger_sha256="e" * 64, yoked_event_sha256=H))
        cases.append(("MOCK_YOKED_SEAL_PIN", dict(yoked=[wrong_seal])))
        for code, kwargs in cases:
            with self.subTest(code=code, case=list(kwargs)), self.assertRaisesRegex(EvidenceError, code):
                self.check(**copy.deepcopy(kwargs))


class EmitterTagsReplaySource(unittest.TestCase):
    def test_staged_replay_ledger_is_tagged_distinctly_and_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def save(name, raw):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); return sha(raw)
            def obj(value): return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
            sp = save("staged/schedule.json", obj(dict(study="B", visit="V1"))); pp = save("staged/package/manifest.json", obj(dict(format="test-only")))
            ledger = b'{"synthetic":"active-ledger"}\n'; lp = save("staged/files/menu_replay_ledger/menus.local.jsonl", ledger)
            own = save("staged/runtime/evidence/joined-" + "e" * 32 + "/menus.local.jsonl", b'{"synthetic":"yoked-ledger"}\n')
            config = dict(files=dict(schedule=dict(path="schedule.json", sha256=sp), package_manifest=dict(path="package/manifest.json", sha256=pp),
                                     menu_replay_ledger=dict(path="files/menu_replay_ledger/menus.local.jsonl", sha256=lp)), pins=dict(package_sha256=H))
            cp = save("staged/join.local.json", obj(config))
            fp = save("fixture-source.json", obj(dict(scope="SIMULATION_TEST", test_only=True)))
            ap = save("cap.json", obj(dict(scope="SIMULATION_TEST", participant_admission=False, acoustic_qualification=False, schedule_sha256=sp, package_sha256=H, fixture_set_sha256=fp)))
            bp = save("build.json", obj(dict(source_commit="a" * 40)))
            rp = save("process.json", obj(dict(version=1, process_id=123, process_exit=0, source_commit="a" * 40, build_manifest_sha256=bp,
                                               started_utc="2026-10-05T00:00:00Z", ended_utc="2026-10-05T00:01:00Z")))
            save("staged/runtime/evidence/export/manifest.json", obj(dict(schema_version="data-export-provisional-1")))
            args = dict(run_id="synthetic-yoked", role="yoked", config="staged/join.local.json", config_sha256=cp, capability="cap.json", capability_sha256=ap,
                        build_manifest="build.json", build_manifest_sha256=bp, process_result="process.json", process_result_sha256=rp,
                        fixture_provenance=root / "fixture-source.json", fixture_provenance_sha256=fp)
            emit(root, **args); m = strict((root / "mock-run.manifest.json").read_bytes())
            kinds = {a["path"]: a["kind"] for a in m["artifacts"]}
            self.assertEqual(kinds["staged/files/menu_replay_ledger/menus.local.jsonl"], "menu_replay_source")
            self.assertEqual([p for p, k in kinds.items() if k == "menu_journal"], ["staged/runtime/evidence/joined-" + "e" * 32 + "/menus.local.jsonl"])
            # A staged copy that differs from the config pin is refused.
            (root / "mock-run.manifest.json").unlink(); (root / "fixture-provenance.local.json").unlink()
            (root / "staged/files/menu_replay_ledger/menus.local.jsonl").write_bytes(b'{"tampered":true}\n')
            with self.assertRaisesRegex(EvidenceError, "MOCK_MENU_REPLAY_SOURCE_PIN"): emit(root, **args)


if __name__ == "__main__":
    unittest.main()

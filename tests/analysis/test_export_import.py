"""Station export -> raw visit folder (#81, #72): verification, mapping, refusals, and the
end-to-end path into ``reconcile`` with the published synthetic exports.

The published samples (``docs/data/synthetic-visit``, ``docs/operator-console/
synthetic-exports``) are verified as published. They name no study visit (``DEMO``) and
three unrelated identities, so the end-to-end import uses a deterministic re-identified
copy: the export identity is bound to the person slot a ``synth-logs`` root reveals, the
journal is re-chained and the manifest rewritten. Nothing here is native visit evidence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from av_analysis import export_import as ei
from av_analysis.cli import main
from av_analysis.fileio import csv_bytes, parse_csv
from av_analysis.paths import DataRoot
from av_analysis.references import revealed_persons
from av_analysis.schemas import validator
from av_analysis.templates import (
    DEVIATIONS_COLUMNS,
    EXPOSURE_LEDGER_COLUMNS,
    EXTENSION_COLUMNS,
    TRIAL_LOG_COLUMNS,
)

REPO = Path(__file__).resolve().parents[2]
PUBLISHED = REPO / "docs" / "data" / "synthetic-visit"
CONSOLE = REPO / "docs" / "operator-console" / "synthetic-exports"
APPARATUS = REPO / "apparatus" / "data"
PUBLISHED_SHA = json.loads((PUBLISHED / "public-manifest.json").read_bytes())[
    "source_export_manifest_sha256"
]
CONSOLE_SHA = {
    e["path"]: e["sha256"] for e in json.loads((CONSOLE / "manifest.json").read_bytes())["files"]
}
OLD_IDENTITY = b"11111111111111111111111111111111,SYNTHETIC,DEMO,synthetic-station"
INTEGRITY_CODES = {
    "RAW_MANIFEST_MISSING",
    "RAW_FILE_MISSING",
    "RAW_FILE_UNLISTED",
    "RAW_HASH_CHANGED",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compact(row: dict) -> bytes:
    return json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode()


def chain(rows: list[dict], previous: str = "0" * 64, start: int = 0) -> tuple[bytes, str]:
    """Journal lines for ``rows`` (sequence from ``start``), each hashed without its hash."""
    out = []
    for n, row in enumerate(rows, start=start):
        body = dict(row, sequence=n, previous_sha256=previous)
        body.pop("sha256", None)
        previous = sha(compact(body) + b"\n")
        out.append(compact({**body, "sha256": previous}) + b"\n")
    return b"".join(out), previous


def write_bundle(
    dst: Path,
    *,
    identity: dict,
    segments: list[bytes],
    head: str,
    records: int,
    tables: dict[str, bytes] | None = None,
    torn: bool = False,
    extra_files: dict[str, bytes] | None = None,
) -> str:
    """A station-layout ExportBundle in ``dst``; returns the manifest SHA-256."""
    original = json.loads((PUBLISHED / "original-export-manifest.json").read_bytes())
    files = {f"raw/events-{i:04d}.local.jsonl": s for i, s in enumerate(segments)}
    for name in ("trial-log.csv", "exposure-ledger.csv", "header-contract.json"):
        files[name] = (tables or {}).get(name, (PUBLISHED / name).read_bytes())
    files.update(extra_files or {})
    for name, data in files.items():
        (dst / name).parent.mkdir(parents=True, exist_ok=True)
        (dst / name).write_bytes(data)
    trials = parse_csv(files["trial-log.csv"])[1]
    plays = parse_csv(files["exposure-ledger.csv"])[1]
    manifest = dict(
        original,
        identity=identity,
        record_count=records,
        last_record_sha256=head,
        trial_rows=len(trials),
        exposure_rows=len(plays),
        unacknowledged_torn_tail=torn,
        files=[{"path": p, "bytes": len(d), "sha256": sha(d)} for p, d in files.items()],
    )
    data = compact(manifest)
    (dst / "manifest.json").write_bytes(data)
    return sha(data)


def published_rows(identity: dict) -> list[dict]:
    rows = [json.loads(line) for line in (PUBLISHED / "events.jsonl").read_bytes().splitlines()]
    return [dict(r, identity=identity) for r in rows]


def reidentify(dst: Path, coded: str, visit: str, **overrides: str) -> tuple[str, dict]:
    """Copy of the published bundle bound to ``coded``/``visit`` (station layout)."""
    original = json.loads((PUBLISHED / "original-export-manifest.json").read_bytes())
    identity = dict(original["identity"], coded_id=coded, visit_id=visit, **overrides)
    segment, head = chain(published_rows(identity))
    new = ",".join(
        identity[k] for k in ("session_id", "coded_id", "visit_id", "station_id")
    ).encode()
    tables = {
        name: (PUBLISHED / name).read_bytes().replace(OLD_IDENTITY, new)
        for name in ("trial-log.csv", "exposure-ledger.csv")
    }
    digest = write_bundle(
        dst, identity=identity, segments=[segment], head=head, records=614, tables=tables
    )
    return digest, identity


@pytest.fixture(scope="module")
def synthetic_template(tmp_path_factory):
    """A one-person Study A SYNTHETIC root (synth-logs) without its D7 raw folder."""
    path = tmp_path_factory.mktemp("synth") / "root"
    args = ["synth-logs", "--demo-seed", "DEMO-o6.1.4-import", "--out", str(path)]
    assert (
        main([*args, "--study", "A", "--set", "pilot", "--units", "1", "--max-persons", "1"]) == 0
    )
    root = DataRoot.open(path)
    ((slot, coded),) = revealed_persons(root).items()
    shutil.rmtree(root.raw_visit_dir(f"{slot}-D7"))
    return path, slot, coded


class Case:
    """One import attempt: root copy, re-identified bundle and console files."""

    def __init__(self, tmp_path: Path, template: tuple[Path, str, str]) -> None:
        src, self.slot, self.coded = template
        self.root_path = tmp_path / "root"
        shutil.copytree(src, self.root_path)
        self.root = DataRoot.open(self.root_path)
        self.visit_id = f"{self.slot}-D7"
        self.export = tmp_path / "export"
        self.export.mkdir()
        self.manifest_sha, self.identity = reidentify(self.export, self.coded, "D7")
        self.console = tmp_path / "console"
        self.console.mkdir()
        sheet = (CONSOLE / "demo-a-run-sheet.csv").read_bytes()
        self.run_sheet = self.console / "run-sheet.csv"
        self.run_sheet.write_bytes(sheet.replace(b"demo-01,D7", f"{self.coded},D7".encode()))
        self.deviations = self.console / "deviations.csv"
        shutil.copy(CONSOLE / "demo-a-deviations.provisional.csv", self.deviations)

    def args(self) -> list[str]:
        return [
            "import-export",
            self.visit_id,
            *("--root", str(self.root_path), "--export", str(self.export)),
            *("--export-manifest-sha256", self.manifest_sha),
            *("--run-sheet", str(self.run_sheet)),
            *("--run-sheet-sha256", sha(self.run_sheet.read_bytes())),
            *("--deviations", str(self.deviations)),
            *("--deviations-sha256", sha(self.deviations.read_bytes())),
        ]

    def run(self, **change: str):
        return ei.import_export(
            self.root,
            change.get("visit_id", self.visit_id),
            self.export,
            change.get("manifest_sha", self.manifest_sha),
            self.run_sheet,
            sha(self.run_sheet.read_bytes()),
            self.deviations,
            sha(self.deviations.read_bytes()),
        )

    def refused(self, code: str, **change: str) -> None:
        with pytest.raises(ei.ExportRefused) as exc:
            self.run(**change)
        assert exc.value.code == code, str(exc.value)
        assert not self.root.raw_visit_dir(self.visit_id).exists()


@pytest.fixture
def case(tmp_path, synthetic_template):
    return Case(tmp_path, synthetic_template)


# ---------------------------------------------------------------------------------------
# Contracts


def test_provisional_columns_match_the_apparatus_headers_and_published_contract():
    for name, columns in (
        ("trial-log.provisional.csv", ei.PROVISIONAL_TRIAL_COLUMNS),
        ("exposure-ledger.provisional.csv", ei.PROVISIONAL_EXPOSURE_COLUMNS),
    ):
        assert parse_csv((APPARATUS / name).read_bytes())[0] == columns
    contract = json.loads((PUBLISHED / "header-contract.json").read_bytes())
    assert contract["trial_headers"] == list(ei.PROVISIONAL_TRIAL_COLUMNS)
    assert contract["exposure_headers"] == list(ei.PROVISIONAL_EXPOSURE_COLUMNS)
    header = parse_csv((CONSOLE / "demo-a-deviations.provisional.csv").read_bytes())[0]
    assert header == ei.CONSOLE_DEVIATION_COLUMNS


def _exported(sources):
    return {s.split(":", 1)[1] for s in sources.values() if s and s.startswith("export:")}


def test_every_template_column_has_exactly_one_documented_source():
    assert tuple(ei.TRIAL_SOURCES) == (*TRIAL_LOG_COLUMNS, *EXTENSION_COLUMNS["trial-log"])
    assert tuple(ei.EXPOSURE_SOURCES) == (
        *EXPOSURE_LEDGER_COLUMNS,
        *EXTENSION_COLUMNS["exposure-ledger"],
    )
    assert tuple(ei.DEVIATION_SOURCES) == DEVIATIONS_COLUMNS
    # Every provisional column is mapped or deliberately kept only under export/.
    assert _exported(ei.TRIAL_SOURCES) | set(ei.TRIAL_EXPORT_ONLY) == set(
        ei.PROVISIONAL_TRIAL_COLUMNS
    )
    assert _exported(ei.EXPOSURE_SOURCES) | set(ei.EXPOSURE_EXPORT_ONLY) == set(
        ei.PROVISIONAL_EXPOSURE_COLUMNS
    )
    console = {s.split(":", 1)[1] for s in ei.DEVIATION_SOURCES.values() if s and "console:" in s}
    assert console | {"reason"} == set(ei.CONSOLE_DEVIATION_COLUMNS)
    kinds = {
        s.split(":", 1)[0]
        for table in (ei.TRIAL_SOURCES, ei.EXPOSURE_SOURCES, ei.DEVIATION_SOURCES)
        for s in table.values()
        if s
    }
    assert kinds == {"export", "binding", "identity", "derived", "journal", "console"}


# ---------------------------------------------------------------------------------------
# Published samples


def test_published_sample_verifies_as_published():
    bundle = ei.read_bundle(PUBLISHED, PUBLISHED_SHA)
    assert (len(bundle.trials), len(bundle.exposures)) == (36, 36)
    assert bundle.journal.records == 614 and bundle.journal.visit_complete
    assert not bundle.journal.torn_tail and len(bundle.journal.requests) == 36
    assert set(bundle.files) == {
        "raw/events-0000.local.jsonl",
        "trial-log.csv",
        "exposure-ledger.csv",
        "header-contract.json",
    }


def test_published_sample_is_refused_when_its_hash_or_index_is_wrong(tmp_path):
    with pytest.raises(ei.ExportRefused) as exc:
        ei.read_bundle(PUBLISHED, "0" * 64)
    assert exc.value.code == "EXPORT_MANIFEST_HASH"
    copy = tmp_path / "published"
    shutil.copytree(PUBLISHED, copy)
    (copy / "stray.txt").write_bytes(b"x")
    with pytest.raises(ei.ExportRefused) as exc:
        ei.read_bundle(copy, PUBLISHED_SHA)
    assert exc.value.code == "EXPORT_INVENTORY"
    (copy / "stray.txt").unlink()
    index = json.loads((copy / "public-manifest.json").read_bytes())
    index["trial_rows"] = 35
    (copy / "public-manifest.json").write_bytes(json.dumps(index).encode())
    with pytest.raises(ei.ExportRefused) as exc:
        ei.read_bundle(copy, PUBLISHED_SHA)
    assert exc.value.code == "EXPORT_LAYOUT"


def test_published_sample_names_no_study_visit_so_it_cannot_be_bound(case):
    bundle = ei.read_bundle(PUBLISHED, PUBLISHED_SHA)
    with pytest.raises(ei.ExportRefused) as exc:
        ei.plan_import(
            case.root,
            case.visit_id,
            bundle,
            case.run_sheet.read_bytes(),
            case.deviations.read_bytes(),
        )
    assert exc.value.code == "EXPORT_VISIT_MISMATCH"
    assert not case.root.raw_visit_dir(case.visit_id).exists()


# ---------------------------------------------------------------------------------------
# End to end


def _rows(path: Path) -> list[dict]:
    header, rows = parse_csv(path.read_bytes())
    return [dict(zip(header, r, strict=True)) for r in rows]


def test_clean_import_reaches_reconcile_with_intact_raw_integrity(case, capsys):
    assert main(case.args()) == 0
    out = capsys.readouterr().out
    assert f"{case.visit_id}: imported 36 trial rows, 36 plays, closed complete" in out
    folder = case.root.raw_visit_dir(case.visit_id)
    manifest = json.loads((folder / "exit-manifest.json").read_bytes())
    assert not list(validator("exit-manifest.schema.json").iter_errors(manifest))
    assert manifest["data_kind"] == "SYNTHETIC" and manifest["closed"] == "complete"
    assert manifest["source"]["manifest_sha256"] == case.manifest_sha
    assert manifest["source"]["headers_qualified"] is False
    listed = {e["path"] for e in manifest["files"]}
    on_disk = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} - {
        "exit-manifest.json"
    }
    assert listed == on_disk
    assert (folder / "export/bundle/manifest.json").read_bytes() == (
        case.export / "manifest.json"
    ).read_bytes()
    assert (folder / "visit-run-sheet.csv").read_bytes() == case.run_sheet.read_bytes()

    trials, plays = _rows(folder / "trial-log.csv"), _rows(folder / "exposure-ledger.csv")
    source = _rows(case.export / "trial-log.csv")
    assert [t["trial_id"] for t in trials] == [s["attempt_id"] for s in source]
    assert {t["study"] for t in trials} == {"A"} and {t["visit"] for t in trials} == {"D7"}
    assert {t["participant_id"] for t in trials} == {case.coded}
    assert {t["batch_id"] for t in trials} == {case.slot[:5]}
    assert {t["trial_type"] for t in trials} == {""}  # no producer source: null
    assert {t["pcm_sha256"] for t in trials} == {"d" * 64}
    assert [p["trial_ref"] for p in plays] == [t["trial_id"] for t in trials]
    assert {p["presentation_index"] for p in plays} == {"1"}
    assert {p["wave"] for p in plays} == {"3"}
    assert {p["accepted_or_rejected"] for p in plays} == {"pending"}  # copied, not repaired
    (deviation,) = _rows(folder / "deviations.csv")
    assert (deviation["operator"], deviation["category"]) == ("ops-demo", "technical")

    assert main(["reconcile", case.visit_id, "--root", str(case.root_path)]) == 1
    report = json.loads(
        (case.root_path / "reconciled" / case.visit_id / "reconciliation.json").read_bytes()
    )
    assert report["raw_unchanged"] is True
    codes = {d["code"] for d in report["discrepancies"]}
    assert not codes & INTEGRITY_CODES
    assert "RAW_FORMAT" in codes  # template columns without a producer source
    assert {i["path"] for i in report["inputs"]} >= {f"raw/{case.visit_id}/{p}" for p in listed}

    # Exclusive create: the same visit is never imported twice.
    assert main(case.args()) == 2
    assert "EXPORT_VISIT_EXISTS" in capsys.readouterr().err


def test_import_is_deterministic(tmp_path, synthetic_template):
    folders = []
    for n in range(2):
        c = Case(tmp_path / str(n), synthetic_template)
        c.run()
        folders.append(c.root.raw_visit_dir(c.visit_id))
    a, b = (
        {p.relative_to(f).as_posix(): p.read_bytes() for p in f.rglob("*") if p.is_file()}
        for f in folders
    )
    assert a == b


# ---------------------------------------------------------------------------------------
# Tamper and drift cases: refused, nothing written


def _flip(path: Path, offset: int = 200) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0x01
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(
    "target",
    ["raw/events-0000.local.jsonl", "trial-log.csv", "exposure-ledger.csv", "header-contract.json"],
)
def test_byte_flip_in_a_bundle_file_is_refused(case, target):
    _flip(case.export / target, 100)
    case.refused("EXPORT_FILE_HASH")


def test_byte_flip_in_the_manifest_is_refused(case):
    _flip(case.export / "manifest.json", 50)
    case.refused("EXPORT_MANIFEST_HASH")


@pytest.mark.parametrize("which", ["run_sheet", "deviations"])
def test_byte_flip_in_a_console_export_is_refused(case, which):
    path = getattr(case, which)
    pinned = sha(path.read_bytes())
    _flip(path, 20)
    with pytest.raises(ei.ExportRefused) as exc:
        ei.read_pinned(path, pinned)
    assert exc.value.code == "EXPORT_FILE_HASH"


@pytest.mark.parametrize("target", ["exposure-ledger.csv", "raw/events-0000.local.jsonl"])
def test_missing_bundle_file_is_refused(case, target):
    (case.export / target).unlink()
    case.refused("EXPORT_INVENTORY")


def test_missing_manifest_or_console_file_is_refused(case, tmp_path):
    (case.export / "manifest.json").unlink()
    case.refused("EXPORT_MANIFEST_MISSING")
    with pytest.raises(ei.ExportRefused) as exc:
        ei.read_pinned(tmp_path / "absent.csv", "0" * 64)
    assert exc.value.code == "EXPORT_FILE_MISSING"
    with pytest.raises(ei.ExportRefused) as exc:
        ei.read_pinned(case.run_sheet, "not-a-hash")
    assert exc.value.code == "EXPORT_HASH_REQUIRED"


def test_extra_file_in_the_bundle_is_refused(case):
    (case.export / "notes.txt").write_bytes(b"x")
    case.refused("EXPORT_INVENTORY")


def _rebuild(case: Case, tables: dict[str, bytes], identity: dict | None = None) -> None:
    """Rewrite the bundle with consistent hashes around changed tables."""
    identity = identity or case.identity
    segment, head = chain(published_rows(identity))
    current = {
        n: (case.export / n).read_bytes()
        for n in ("trial-log.csv", "exposure-ledger.csv", "header-contract.json")
    }
    case.manifest_sha = write_bundle(
        case.export,
        identity=identity,
        segments=[segment],
        head=head,
        records=614,
        tables={**current, **tables},
    )


def _renamed(data: bytes) -> bytes:
    return data.replace(b"response_action\n", b"response_actions\n", 1)


def _dropped(data: bytes) -> bytes:  # the last column, header and rows
    return b"\n".join(line.rsplit(b",", 1)[0] if line else line for line in data.split(b"\n"))


def _unknown(data: bytes) -> bytes:  # an extra first column
    lines = data.split(b"\n")
    return b"\n".join(
        [b"extra," + lines[0]] + [b"x," + line if line else line for line in lines[1:]]
    )


@pytest.mark.parametrize(
    ("table", "drift"),
    [
        ("trial-log.csv", _renamed),
        ("trial-log.csv", _dropped),
        ("exposure-ledger.csv", _dropped),
        ("exposure-ledger.csv", _unknown),
    ],
)
def test_column_drift_with_consistent_hashes_is_refused(case, table, drift):
    _rebuild(case, {table: drift((case.export / table).read_bytes())})
    case.refused("EXPORT_COLUMNS")


def test_header_contract_drift_is_refused(case):
    contract = json.loads((case.export / "header-contract.json").read_bytes())
    contract["trial_headers"] = contract["trial_headers"][::-1]
    _rebuild(case, {"header-contract.json": compact(contract)})
    case.refused("EXPORT_COLUMNS")


def test_console_column_drift_is_refused(case):
    data = case.deviations.read_bytes().replace(b"staff_code", b"staff")
    case.deviations.write_bytes(data)
    case.refused("EXPORT_COLUMNS")
    sheet = case.run_sheet.read_bytes().replace(b"phone_locked", b"phone")
    case.deviations.write_bytes((CONSOLE / "demo-a-deviations.provisional.csv").read_bytes())
    case.run_sheet.write_bytes(sheet)
    case.refused("EXPORT_COLUMNS")


def test_unknown_deviation_reason_is_refused(case):
    data = case.deviations.read_bytes().replace(b",technical,", b",weather,")
    case.deviations.write_bytes(data)
    case.refused("EXPORT_DEVIATION_REASON")


def test_visit_id_mismatches_are_refused(case):
    case.refused("EXPORT_VISIT_MISMATCH", visit_id=f"{case.slot}-D0")
    other = "A-P99-L01-D7"
    case.refused("EXPORT_VISIT_MISMATCH", visit_id=other)
    case.refused("EXPORT_VISIT_ID", visit_id="DEMO")
    case.run_sheet.write_bytes(case.run_sheet.read_bytes().replace(b",D7,", b",D0,"))
    case.refused("EXPORT_VISIT_MISMATCH")


def test_csv_identity_differing_from_the_manifest_is_refused(case):
    table = (case.export / "trial-log.csv").read_bytes()
    first, _, rest = table.partition(b"\n")
    rows = rest.split(b"\n")
    rows[3] = rows[3].replace(b",D7,", b",D0,", 1)
    _rebuild(case, {"trial-log.csv": first + b"\n" + b"\n".join(rows)})
    case.refused("EXPORT_IDENTITY")


def test_unrevealed_coded_id_is_refused(case, tmp_path):
    case.manifest_sha, case.identity = reidentify(_fresh(tmp_path / "other"), "DEMO-A9999", "D7")
    case.export = tmp_path / "other"
    case.refused("EXPORT_IDENTITY_UNBOUND")


def _fresh(path: Path) -> Path:
    path.mkdir()
    return path


def test_rehashed_journal_edit_breaks_the_chain(case):
    segment = (case.export / "raw/events-0000.local.jsonl").read_bytes()
    lines = segment.splitlines(keepends=True)
    lines[5] = lines[5].replace(b'"host_mono_ms":0.0', b'"host_mono_ms":1.0', 1)
    tables = {n: (case.export / n).read_bytes() for n in ("trial-log.csv", "exposure-ledger.csv")}
    case.manifest_sha = write_bundle(
        case.export,
        identity=case.identity,
        segments=[b"".join(lines)],
        head=json.loads(lines[-1])["sha256"],
        records=614,
        tables=tables,
    )
    case.refused("EXPORT_JOURNAL_HASH")


def test_unmapped_lesson_table_is_refused(case):
    tables = {n: (case.export / n).read_bytes() for n in ("trial-log.csv", "exposure-ledger.csv")}
    segment, head = chain(published_rows(case.identity))
    case.manifest_sha = write_bundle(
        case.export,
        identity=case.identity,
        segments=[segment],
        head=head,
        records=614,
        tables=tables,
        extra_files={"lesson-exposures.csv": b"x\n", "lesson-header-contract.json": b"{}"},
    )
    case.refused("EXPORT_UNMAPPED_TABLE")


def test_record_count_and_link_disagreements_are_refused(case):
    tables = {n: (case.export / n).read_bytes() for n in ("trial-log.csv", "exposure-ledger.csv")}
    segment, head = chain(published_rows(case.identity))
    case.manifest_sha = write_bundle(
        case.export,
        identity=case.identity,
        segments=[segment],
        head=head,
        records=613,
        tables=tables,
    )
    case.refused("EXPORT_JOURNAL_CHAIN")
    # A play moved to another attempt: hashes consistent, journal link broken.
    moved = tables["exposure-ledger.csv"].replace(
        b",SYNTHETIC-0,SYNTHETIC-0,", b",SYNTHETIC-1,SYNTHETIC-1,", 1
    )
    _rebuild(case, {"exposure-ledger.csv": moved})
    case.refused("EXPORT_LINK")


# ---------------------------------------------------------------------------------------
# Torn tails and data kinds


def _torn(case: Case, *, acknowledged: bool, flag: bool) -> None:
    rows = published_rows(case.identity)
    head_rows, tail_rows = rows[:300], rows[300:]
    seg0, previous = chain(head_rows)
    tail = b'{"schema_version":"data-events-provisional-1","seq'
    seg0 += tail
    segments = [seg0]
    count = len(head_rows)
    if acknowledged:
        recovery = dict(
            rows[0],
            event_id="f" * 32,
            event_type="recovery",
            opportunity_id=None,
            attempt_id=None,
            audio_request_id=None,
            payload={
                "preserved_tails": [
                    {
                        "segment": "events-0000.local.jsonl",
                        "tail_offset": len(seg0) - len(tail),
                        "tail_sha256": sha(tail),
                        "segment_sha256": sha(seg0),
                    }
                ]
            },
        )
        seg1, previous = chain([recovery, *tail_rows], previous, start=count)
        segments.append(seg1)
        count += 1 + len(tail_rows)
    tables = {n: (case.export / n).read_bytes() for n in ("trial-log.csv", "exposure-ledger.csv")}
    if not acknowledged:  # the plays after the tail never reached the journal
        tables = _truncate(
            tables, {r["audio_request_id"] for r in head_rows if r["event_type"] == "audio_request"}
        )
    case.manifest_sha = write_bundle(
        case.export,
        identity=case.identity,
        segments=segments,
        head=previous,
        records=count,
        tables=tables,
        torn=flag,
    )


def _truncate(tables: dict[str, bytes], requests: set[str]) -> dict[str, bytes]:
    """Keep the plays whose audio request reached the journal, and their attempts."""
    play_header, plays = parse_csv(tables["exposure-ledger.csv"])
    kept = [p for p in plays if p[play_header.index("audio_request_id")] in requests]
    attempts = {p[play_header.index("attempt_id")] for p in kept}
    trial_header, trials = parse_csv(tables["trial-log.csv"])
    column = trial_header.index("attempt_id")
    return {
        "trial-log.csv": csv_bytes(trial_header, [t for t in trials if t[column] in attempts]),
        "exposure-ledger.csv": csv_bytes(play_header, kept),
    }


def test_unacknowledged_torn_tail_imports_as_interrupted(case):
    _torn(case, acknowledged=False, flag=True)
    plan = case.run()
    assert plan.exit_manifest["closed"] == "interrupted"
    assert plan.exit_manifest["source"]["unacknowledged_torn_tail"] is True


def test_acknowledged_torn_tail_imports_as_complete(case):
    _torn(case, acknowledged=True, flag=False)
    plan = case.run()
    assert plan.exit_manifest["closed"] == "complete"
    assert plan.exit_manifest["source"]["record_count"] == 615


def test_torn_tail_disagreeing_with_the_manifest_is_refused(case):
    _torn(case, acknowledged=False, flag=False)
    case.refused("EXPORT_JOURNAL_TAIL")


def test_real_root_refuses_synthetic_or_unqualified_exports(
    tmp_path, monkeypatch, synthetic_template
):
    root = DataRoot.create(tmp_path / "real", "REAL", label="o6-1-4-test")
    _, slot, coded = synthetic_template
    export = _fresh(tmp_path / "export")
    digest, _ = reidentify(export, coded, "D7")
    bundle = ei.read_bundle(export, digest)
    monkeypatch.setattr(ei, "revealed_persons", lambda r: {slot: coded})
    with pytest.raises(ei.ExportRefused) as exc:
        ei.bind(root, bundle, f"{slot}-D7")
    assert exc.value.code == "EXPORT_KIND_MISMATCH"
    plain = _fresh(tmp_path / "plain")
    digest, _ = reidentify(plain, "P0001", "D7", session_id="2" * 32, station_id="st-1")
    monkeypatch.setattr(ei, "revealed_persons", lambda r: {slot: "P0001"})
    bundle = ei.read_bundle(plain, digest)
    with pytest.raises(ei.ExportRefused) as exc:
        ei.bind(root, bundle, f"{slot}-D7")
    assert exc.value.code == "EXPORT_UNQUALIFIED"
    synthetic = DataRoot.open(synthetic_template[0])
    with pytest.raises(ei.ExportRefused) as exc:
        ei.bind(synthetic, bundle, f"{slot}-D7")
    assert exc.value.code == "EXPORT_KIND_MISMATCH"


def test_cli_refuses_a_missing_root(tmp_path, capsys):
    args = ["import-export", "A-P01-L01-D7", "--root", str(tmp_path / "none")]
    args += ["--export", "e", "--export-manifest-sha256", "0" * 64]
    args += ["--run-sheet", "s", "--run-sheet-sha256", "0" * 64]
    args += ["--deviations", "d", "--deviations-sha256", "0" * 64]
    assert main(args) == 2
    assert "refusing" in capsys.readouterr().err


def test_strict_json_refuses_duplicates_bom_and_nonfinite():
    for data in (b'{"a":1,"a":2}', b"\xef\xbb\xbf{}", b'{"a":NaN}', b"\xff", b"{"):
        with pytest.raises(ei.ExportRefused):
            ei.strict_json(data, "x")

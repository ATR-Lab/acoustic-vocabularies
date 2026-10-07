"""Bank registers, the set guard for loading a bank, and archives (#27; reused by #28)."""

import json
import os
import shutil

import pytest
from av_generation.bank_manifest import BankSetError
from av_generation.clock import ManualClock
from av_generation.jsonio import file_set_sha256, file_sha256, read_json

from av_banks.archive import (
    ARCHIVE_HASH_NAME,
    ARCHIVE_MANIFEST_NAME,
    ArchiveError,
    archive_problems,
    archive_tree,
    archived_files,
    is_read_only,
    make_writable,
    read_archive_manifest,
)
from av_banks.layout import BankLayout
from av_banks.manifest import ManifestError
from av_banks.register import (
    REGISTER_COLUMNS,
    RegisterError,
    RegisterRow,
    bank_reason,
    check_bank_id_set,
    open_bank,
    read_register,
    register_problems,
    register_row,
    relative_path,
    run_id_of,
    write_register,
)
from av_banks.run import build_banks


@pytest.fixture(scope="module")
def bank_run(kit, tmp_path_factory):
    """One DEMO bank inside a run directory (complete, attempt 1)."""
    root = tmp_path_factory.mktemp("register")
    result = build_banks(
        [kit.spec("DEMO-bank-31")],
        runs_root=root / "runs",
        run_id="DEMO-register-run",
        config=kit.config,
        proposer=kit.proposer(kit.script(kit.all_kind("valid"), tag="reg")),
        clock=ManualClock(),
        workers=1,
        ledger_factory=kit.Ledger,
        fsync=False,
    )
    return root, result.banks[0]


@pytest.fixture
def run_copy(bank_run, tmp_path):
    root, bank = bank_run
    target = tmp_path / "copy"
    shutil.copytree(root, target)
    return target, target / "runs" / "DEMO-register-run" / "banks" / bank.bank_id


def _row(root, bank_dir, **kwargs):
    values = {"role": "main", "use": True, "verify_ok": True, **kwargs}
    return register_row(bank_dir, root=root, **values)


def test_register_row_from_a_bank(bank_run, kit):
    root, bank = bank_run
    row = _row(root, bank.bank_dir)
    assert row.bank_id == "DEMO-bank-31" and row.bank_version == "1.0.0"
    assert row.dyad_slot == kit.permutation.unit_id and row.set == "demo"
    assert (row.status, row.attempt_used, row.attempts) == ("complete", 1, 1)
    assert row.slots_used == bank.slots_used == 192
    assert row.bank_sha256 == bank.bank_sha256
    assert row.generation_config_sha256 == kit.config.frozen_sha256()
    assert row.separation_threshold == "0.10" and row.reason == "complete at attempt 1"
    assert row.run_id == "DEMO-register-run" == run_id_of(bank.bank_dir)
    assert row.bank_path == "runs/DEMO-register-run/banks/DEMO-bank-31"
    assert row.values()[REGISTER_COLUMNS.index("use")] == "1"


def test_register_round_trip_and_checks(bank_run, tmp_path):
    root, bank = bank_run
    row = _row(root, bank.bank_dir)
    path = tmp_path / "register.csv"
    digest = write_register([row], path)
    assert len(digest) == 64
    assert path.read_bytes().count(b"\n") == 2 and b"\r" not in path.read_bytes()
    assert read_register(path) == (row,)
    assert register_problems(path, root, expected_set="demo", rerun_verify=True) == ()
    problems = register_problems(path, root, expected_set="pilot")
    assert any("demo bank; pilot mode refuses it" in p for p in problems)
    assert any("set 'demo', the register holds pilot banks" in p for p in problems)


def test_register_problems_name_every_wrong_column(bank_run, tmp_path):
    root, bank = bank_run
    good = _row(root, bank.bank_dir)
    bad = RegisterRow(
        **{
            **{name: getattr(good, name) for name in REGISTER_COLUMNS},
            "bank_sha256": "0" * 64,
            "slots_used": 1,
            "attempt_used": 2,
            "separation_threshold": "0.20",
            "run_id": "DEMO-other",
            "verify": "fail",
        }
    )
    path = tmp_path / "register.csv"
    write_register([bad, good], path)
    problems = " | ".join(register_problems(path, root, expected_set="demo", rerun_verify=True))
    assert f"register hash {'0' * 64} != recomputed {good.bank_sha256}" in problems
    for name in ("slots_used", "attempt_used", "separation_threshold"):
        assert f"{name} " in problems
    assert "run_id differs from the run manifest" in problems
    assert "only a complete bank that verifies can be used" in problems
    assert "verify now passes" in problems
    assert "DEMO-bank-31 v1.0.0: listed 2 times" in problems
    assert "2 banks in use" in problems
    missing = RegisterRow(
        **{**{n: getattr(good, n) for n in REGISTER_COLUMNS}, "bank_path": "no/such"}
    )
    write_register([missing], path)
    assert "no/such" in " | ".join(register_problems(path, root, expected_set="demo"))


def test_a_bank_hash_file_that_differs_breaks_the_register(run_copy, tmp_path):
    root, bank_dir = run_copy
    path = tmp_path / "register.csv"
    write_register([_row(root, bank_dir)], path)
    assert register_problems(path, root, expected_set="demo") == ()
    BankLayout(bank_dir).bank_hash.write_bytes(b"0" * 64 + b"\n")  # manifest and files agree
    assert register_problems(path, root, expected_set="demo") == (
        "DEMO-bank-31 v1.0.0: stored manifest or bank-sha256.txt differs from the files",
    )


def test_malformed_registers_are_refused(bank_run, tmp_path):
    root, bank = bank_run
    path = tmp_path / "register.csv"
    path.write_text("bank_id,status\n", encoding="utf-8")
    with pytest.raises(RegisterError, match="header"):
        read_register(path)
    assert register_problems(path, root, expected_set="demo")[0].startswith("register:")
    header = ",".join(REGISTER_COLUMNS)
    good = ",".join(_row(root, bank.bank_dir).values())
    path.write_text(f"{header}\n{good},extra\n", encoding="utf-8")
    with pytest.raises(RegisterError, match="line 2"):
        read_register(path)
    for column, value in (("use", "yes"), ("attempts", "x"), ("role", "reserve"), ("verify", "?")):
        cells = good.split(",")
        cells[REGISTER_COLUMNS.index(column)] = value
        path.write_text(f"{header}\n{','.join(cells)}\n", encoding="utf-8")
        with pytest.raises(RegisterError, match=column):
            read_register(path)


def test_rows_need_a_run_directory(kit, built_bank, tmp_path):
    with pytest.raises(RegisterError, match="no run manifest"):
        register_row(built_bank.bank_dir, root=built_bank.bank_dir.parent, role="main",
                     use=True, verify_ok=True)  # fmt: skip
    with pytest.raises(RegisterError, match="not inside the register root"):
        relative_path(built_bank.bank_dir, tmp_path)
    with pytest.raises(RegisterError):
        register_row(tmp_path, root=tmp_path, role="main", use=False, verify_ok=False)


def test_bank_reason(built_bank):
    assert bank_reason(built_bank.manifest) == "complete at attempt 1"


def test_open_bank_checks_the_set_and_the_bank_hash(bank_run, run_copy):
    _, bank = bank_run
    assert open_bank(bank.bank_dir, mode="demo").bank_id == "DEMO-bank-31"
    for mode in ("pilot", "confirmatory"):
        with pytest.raises(BankSetError, match=f"is a demo bank; {mode} mode refuses it"):
            open_bank(bank.bank_dir, mode=mode)
    _, bank_dir = run_copy
    BankLayout(bank_dir).bank_hash.write_text("0" * 64 + "\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="bank-sha256.txt holds"):
        open_bank(bank_dir, mode="demo")


def test_bank_id_set_guard():
    check_bank_id_set("bank-C072", "confirmatory")
    check_bank_id_set("bank-P001", "pilot")
    check_bank_id_set("DEMO-bank-P001", "demo")
    for bank_id in ("bank-P001", "PILOT-B-01", "DEMO-bank-C001", "bank-X001"):
        with pytest.raises(BankSetError, match="confirmatory mode refuses it"):
            check_bank_id_set(bank_id, "confirmatory")


# -- archive -----------------------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "x.jsonl").write_bytes(b'{"x": 1}\n')  # bytes: no CRLF on Windows
    (root / "top.csv").write_bytes(b"h\n1\n")
    yield root
    make_writable(root)


def test_archive_hashes_and_freezes_every_file(tree):
    result = archive_tree(tree, label="DEMO", created_utc="2026-12-14T00:00:00Z")
    assert set(result.files) == {"a/b/x.jsonl", "top.csv"} and result.n_files == 2
    assert result.archive_sha256 == file_set_sha256(result.files)
    assert result.bytes == len('{"x": 1}\n') + len("h\n1\n")
    doc = read_archive_manifest(tree)
    assert doc["label"] == "DEMO" and doc["files"] == dict(result.files)
    assert all(is_read_only(p) for p in tree.rglob("*") if p.is_file())
    assert archive_problems(tree) == ()
    assert archived_files(tree) == dict(result.files)
    with pytest.raises(ArchiveError, match="already archived"):
        archive_tree(tree, label="DEMO", created_utc="x")


def test_archive_problems(tree):
    archive_tree(tree, label="DEMO", created_utc="2026-12-14T00:00:00Z")
    target = tree / "top.csv"
    os.chmod(target, 0o644)
    target.write_text("h\n2\n", encoding="utf-8")
    gone = tree / "a" / "b" / "x.jsonl"
    os.chmod(gone, 0o644)
    gone.unlink()
    (tree / "extra.txt").write_text("x", encoding="utf-8")
    problems = " | ".join(archive_problems(tree))
    assert "top.csv: changed since archiving" in problems and "top.csv: writable" in problems
    assert "extra.txt: not in the archive" in problems
    assert "a/b/x.jsonl: missing" in problems
    os.chmod(tree / ARCHIVE_HASH_NAME, 0o644)
    (tree / ARCHIVE_HASH_NAME).write_text("0" * 64 + "\n", encoding="utf-8")
    assert "archive hash" in " | ".join(archive_problems(tree))
    os.chmod(tree / ARCHIVE_MANIFEST_NAME, 0o644)
    doc = read_json(tree / ARCHIVE_MANIFEST_NAME)
    (tree / ARCHIVE_MANIFEST_NAME).write_text('{"format": "other"}', encoding="utf-8")
    assert archive_problems(tree)[0].startswith("archive:")
    doc["files"] = {"bad\\path": "0" * 64}
    (tree / ARCHIVE_MANIFEST_NAME).write_text(json.dumps(doc), encoding="utf-8")
    assert archive_problems(tree)[0].startswith("archive:")
    doc["files"] = {"top.csv": "0" * 64}
    doc["format_version"] = 2
    (tree / ARCHIVE_MANIFEST_NAME).write_text(json.dumps(doc), encoding="utf-8")
    assert "unsupported version" in archive_problems(tree)[0]


def test_archive_of_an_empty_tree_is_refused(tmp_path):
    with pytest.raises(ArchiveError, match="nothing to archive"):
        archive_tree(tmp_path, label="DEMO", created_utc="x")
    assert archive_problems(tmp_path)[0].startswith("archive:")


def test_counts_in_the_archive_manifest_are_checked(tree):
    archive_tree(tree, label="DEMO", created_utc="2026-12-14T00:00:00Z")
    doc = read_json(tree / ARCHIVE_MANIFEST_NAME)
    doc["n_files"] = 5
    os.chmod(tree / ARCHIVE_MANIFEST_NAME, 0o644)
    (tree / ARCHIVE_MANIFEST_NAME).write_text(json.dumps(doc), encoding="utf-8")
    assert "n_files 5 != 2 listed" in " | ".join(archive_problems(tree))


def test_append_only_logs_may_grow_after_the_archive(tree):
    log = tree / "a" / "b" / "log.jsonl"
    log.write_bytes(b'{"n": 1}\n')
    patterns = ["c/*/log.jsonl", "a/*/log.jsonl"]
    result = archive_tree(tree, label="DEMO", created_utc="x", append_only=patterns)
    assert result.files["a/b/log.jsonl"] == file_sha256(log)
    doc = read_archive_manifest(tree)
    assert doc["append_only"] == {
        "patterns": ["a/*/log.jsonl", "c/*/log.jsonl"],
        "bytes": {"a/b/log.jsonl": 9},
    }
    assert not is_read_only(log) and is_read_only(tree / "top.csv")
    with open(log, "ab") as handle:
        handle.write(b'{"n": 2}\n')
    (tree / "c" / "d").mkdir(parents=True)
    (tree / "c" / "d" / "log.jsonl").write_bytes(b"{}\n")  # a new log where one may start
    assert archive_problems(tree, append_only=patterns) == ()
    assert archive_problems(tree) == ()  # the manifest's own policy
    # the policy cannot be widened by editing the manifest
    narrow = archive_problems(tree, append_only=["a/*/log.jsonl"])
    assert narrow[0].startswith("archive: append-only patterns ['a/*/log.jsonl', 'c/*/log.jsonl']")
    # only matching paths are logs (`*` stays inside one path component)
    (tree / "c" / "log.jsonl").write_bytes(b"{}\n")
    (tree / "c" / "d" / "e").mkdir()
    (tree / "c" / "d" / "e" / "log.jsonl").write_bytes(b"{}\n")
    problems = archive_problems(tree, append_only=patterns)
    assert problems == ("c/d/e/log.jsonl: not in the archive", "c/log.jsonl: not in the archive")
    for name in ("c/log.jsonl", "c/d/e/log.jsonl"):
        os.remove(tree.joinpath(*name.split("/")))
    # the archived bytes are pinned: no edit, no truncation, no removal
    for data in (b'{"n": 9}\n{"n": 2}\n', b'{"n"'):
        log.write_bytes(data)
        assert archive_problems(tree, append_only=patterns) == (
            "a/b/log.jsonl: append-only log changed before its archived end",
        )
    os.remove(log)
    assert archive_problems(tree, append_only=patterns) == ("a/b/log.jsonl: missing",)


def test_a_log_dropped_from_the_append_only_sizes_is_frozen(tree):
    log = tree / "a" / "b" / "log.jsonl"
    log.write_bytes(b"{}\n")
    archive_tree(tree, label="DEMO", created_utc="x", append_only=["a/*/log.jsonl"])
    doc = read_json(tree / ARCHIVE_MANIFEST_NAME)
    doc["append_only"]["bytes"] = {}
    os.chmod(tree / ARCHIVE_MANIFEST_NAME, 0o644)
    (tree / ARCHIVE_MANIFEST_NAME).write_text(json.dumps(doc), encoding="utf-8")
    log.write_bytes(b"{}\n{}\n")
    problems = archive_problems(tree, append_only=["a/*/log.jsonl"])
    assert "a/b/log.jsonl: changed since archiving" in problems
    doc["append_only"] = {"patterns": "a/*/log.jsonl", "bytes": {}}
    (tree / ARCHIVE_MANIFEST_NAME).write_text(json.dumps(doc), encoding="utf-8")
    assert archive_problems(tree) == (
        "archive: archive-manifest.json: malformed append_only section",
    )
    del doc["append_only"]  # a manifest without the section: nothing is append-only
    (tree / ARCHIVE_MANIFEST_NAME).write_text(json.dumps(doc), encoding="utf-8")
    problems = archive_problems(tree)
    assert "a/b/log.jsonl: changed since archiving" in problems


@pytest.mark.parametrize(
    "pattern",
    ["", "/abs/log.jsonl", "a\\b", "a//b", "../log.jsonl", "a/./b", "archive-sha256.txt",
     "*.json", 7],
)  # fmt: skip
def test_bad_append_only_patterns_are_refused(tree, pattern):
    with pytest.raises(ArchiveError, match="append-only pattern"):
        archive_tree(tree, label="DEMO", created_utc="x", append_only=[pattern])
    assert not (tree / ARCHIVE_MANIFEST_NAME).exists()


def test_append_only_takes_a_sequence(tree):
    with pytest.raises(ArchiveError, match="sequence of patterns"):
        archive_tree(tree, label="DEMO", created_utc="x", append_only="a/*/log.jsonl")

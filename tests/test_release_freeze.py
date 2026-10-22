"""Synthetic tests for v1.0 change-log coverage and freeze writing/verification.

Every repository, bundle and concealed file here is generated in a temporary
directory. Nothing is a real freeze, build or allocation.
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tools import quest_station
from tools import release_freeze as rf
from tools import release_freeze_verify as rv
from tools import release_policy as rp

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = "docs/protocol/changes-v1.0.csv"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Synthetic",
                           "-c", "user.email=synthetic@example.invalid", "-c", "commit.gpgsign=false",
                           "-c", "tag.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True).stdout.decode().strip()


def commit_files(repo, files, message, remove=()):
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        git(repo, "add", "--", name)
    for name in remove:
        git(repo, "rm", "-q", "--", name)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def changelog_bytes(rows, newline="\n", bom=False):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator=newline)
    writer.writerow(rf.CHANGELOG_COLUMNS)
    for row in rows:
        writer.writerow([row[c] for c in rf.CHANGELOG_COLUMNS])
    return (b"\xef\xbb\xbf" if bom else b"") + stream.getvalue().encode("utf-8")


def row(change_id, paths, commit, **overrides):
    value = {"change_id": change_id, "component": "app", "paths": paths,
             "description": "Synthetic change", "pilot_finding_id": "PF-A-01", "trigger": "none",
             "issue": "#85", "reason": "Synthetic pilot reason", "checks_rerun": "integrity;leakage",
             "commit": commit, "reviewer": "PENDING"}
    value.update(overrides)
    return value


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    base = commit_files(repo, {"a.txt": b"a\n", "c.txt": b"c\n", "d.txt": b"d\n", "keep.txt": b"k\n"}, "v0.9")
    git(repo, "tag", "v0.9-synthetic")
    first = commit_files(repo, {"a.txt": b"a2\n", "b.txt": b"b\n"}, "fix one")
    git(repo, "mv", "d.txt", "e.txt")
    second = commit_files(repo, {}, "fix two", remove=["c.txt"])
    rows = [row("CHG-001", "a.txt;b.txt", first),
            row("CHG-002", "c.txt;d.txt;e.txt", second, component="protocol-text", trigger="booking-overrun",
                issue="https://github.com/ATR-Lab/acoustic-vocabularies/issues/85",
                checks_rerun="mock-segment", reviewer="github:reviewer-1", pilot_finding_id="PF-A-02")]
    candidate = commit_files(repo, {CHANGELOG: changelog_bytes(rows)}, "change log")
    return {"repo": repo, "base": base, "first": first, "second": second, "candidate": candidate, "rows": rows}


def check(history, rows=None, data=None, **kwargs):
    if rows is not None:
        data = changelog_bytes(rows)
    return rf.check_changelog(history["repo"], "v0.9-synthetic", history["candidate"], CHANGELOG,
                              data=data, **kwargs)


def codes(summary):
    return [p["code"] for p in summary["problems"]]


# ------------------------------------------------------------------ change log

def test_changelog_coverage_pass_reads_committed_file(history):
    summary = check(history)
    assert summary["result"] == "PASS", summary["problems"]
    assert summary["changed_paths"] == 6  # a b c d e + the change log itself
    assert summary["exempt_paths"] == [CHANGELOG]
    assert summary["covered_paths"] == 5 and summary["rows"] == 2
    assert summary["reviewer_pending"] == 1
    assert summary["base"] == history["base"] and summary["candidate"] == history["candidate"]


def test_reviewer_placeholder_refused_when_review_required(history):
    summary = check(history, require_reviewed=True)
    assert codes(summary) == ["REVIEWER_PENDING"]


def test_uncovered_change_refused(history):
    rows = [dict(history["rows"][0]), dict(history["rows"][1], paths="c.txt;d.txt")]
    summary = check(history, rows)
    assert summary["result"] == "FAIL"
    assert summary["problems"] == [{"code": "UNCOVERED_PATH", "path": "e.txt"}]


def test_extra_row_refused(history):
    rows = history["rows"] + [row("CHG-003", "keep.txt", history["first"])]
    assert check(history, rows)["problems"] == [
        {"code": "EXTRA_ROW_PATH", "change_id": "CHG-003", "path": "keep.txt"}]
    rows = history["rows"] + [row("CHG-003", "never/existed.txt", history["first"])]
    assert codes(check(history, rows)) == ["EXTRA_ROW_PATH"]
    rows = history["rows"] + [row("CHG-003", CHANGELOG, history["first"])]
    assert codes(check(history, rows)) == ["CHANGELOG_LISTS_ITSELF"]


def test_path_in_two_rows_refused(history):
    rows = history["rows"] + [row("CHG-003", "a.txt", history["first"])]
    assert codes(check(history, rows)) == ["PATH_IN_MULTIPLE_ROWS"]


def test_commit_must_lie_between_base_and_candidate(history):
    for commit in (history["base"], "f" * 40):
        rows = [dict(history["rows"][0], commit=commit), history["rows"][1]]
        assert codes(check(history, rows)) == ["COMMIT_OUTSIDE_RANGE"], commit
    rows = [dict(history["rows"][0], commit=history["first"] + ";" + history["second"]), history["rows"][1]]
    assert check(history, rows)["result"] == "PASS"


@pytest.mark.parametrize("field,value,code", [
    ("reason", "", "CELL_INVALID"),
    ("issue", "", "ISSUE_REQUIRED"),
    ("issue", "85", "CELL_INVALID"),
    ("issue", "https://example.invalid/issues/85", "CELL_INVALID"),
    ("reviewer", "", "CELL_INVALID"),
    ("reviewer", "@someone", "CELL_UNSAFE"),
    ("pilot_finding_id", "TBD", "PILOT_FINDING_REQUIRED"),
    ("pilot_finding_id", "none", "PILOT_FINDING_REQUIRED"),
    ("pilot_finding_id", "", "PILOT_FINDING_REQUIRED"),
    ("description", "=HYPERLINK(1)", "CELL_UNSAFE"),
    ("description", "two\nlines", "CELL_UNSAFE"),
    ("checks_rerun", "", "CELL_INVALID"),
    ("commit", "abc", "CELL_INVALID"),
    ("paths", "../a.txt", "PATH_INVALID"),
    ("paths", "a.txt;;b.txt", "CELL_INVALID"),
    ("paths", "a.txt;a.txt", "DUPLICATE_IN_CELL"),
    ("change_id", "1", "CELL_INVALID"),
])
def test_required_fields_and_cell_safety(history, field, value, code):
    rows = [dict(history["rows"][0], **{field: value}), history["rows"][1]]
    with pytest.raises(rf.ChangeLogFault, match="^" + code):
        check(history, rows)


def engineering_row(history, **overrides):
    value = dict(history["rows"][0], trigger="engineering", pilot_finding_id="",
                 reason="Engineering-only fix with no pilot finding")
    value.update(overrides)
    return value


@pytest.mark.parametrize("issue", ["#204", "https://github.com/ATR-Lab/acoustic-vocabularies/issues/204"])
def test_engineering_row_without_finding_accepted_with_issue(history, issue):
    rows = [engineering_row(history, issue=issue), history["rows"][1]]
    summary = check(history, rows)
    assert summary["result"] == "PASS", summary["problems"]
    assert summary["engineering_rows"] == 1 and summary["covered_paths"] == 5
    assert check(history)["engineering_rows"] == 0


def test_engineering_row_without_issue_refused(history):
    rows = [engineering_row(history, issue=""), history["rows"][1]]
    with pytest.raises(rf.ChangeLogFault, match="^ISSUE_REQUIRED: row 2"):
        check(history, rows)
    rows = [engineering_row(history, issue="204"), history["rows"][1]]
    with pytest.raises(rf.ChangeLogFault, match="^CELL_INVALID: row 2 issue"):
        check(history, rows)


@pytest.mark.parametrize("trigger", ["none", "booking-overrun", "Engineering", "engineering-fix", " engineering"])
@pytest.mark.parametrize("finding", ["", "TBD", "none", "n/a"])
def test_non_engineering_row_still_needs_real_finding(history, trigger, finding):
    rows = [dict(history["rows"][0], trigger=trigger, pilot_finding_id=finding), history["rows"][1]]
    with pytest.raises(rf.ChangeLogFault, match="^PILOT_FINDING_REQUIRED: row 2"):
        check(history, rows)


@pytest.mark.parametrize("finding", ["PF-A-01", "none", "TBD", " "])
def test_engineering_row_with_finding_refused(history, finding):
    # Decision: an engineering row never carries a finding. A change that answers
    # a pilot finding is logged under its pilot trigger (or `none`) instead.
    rows = [engineering_row(history, pilot_finding_id=finding), history["rows"][1]]
    with pytest.raises(rf.ChangeLogFault, match="^ENGINEERING_ROW_HAS_FINDING: row 2"):
        check(history, rows)


def test_structure_refusals(history):
    with pytest.raises(rf.ChangeLogFault, match="DUPLICATE_CHANGE_ID"):
        check(history, [history["rows"][0], dict(history["rows"][1], change_id="CHG-001")])
    with pytest.raises(rf.ChangeLogFault, match="CHANGELOG_COLUMNS_MISMATCH"):
        check(history, data=changelog_bytes(history["rows"]).replace(b",reviewer", b",signoff"))
    with pytest.raises(rf.ChangeLogFault, match="CHANGELOG_EMPTY"):
        check(history, rows=[])
    with pytest.raises(rf.ChangeLogFault, match="ROW_WIDTH_INVALID"):
        check(history, data=changelog_bytes(history["rows"]) + b"CHG-009,app\n")
    with pytest.raises(rf.ChangeLogFault, match="REF_UNRESOLVED"):
        rf.check_changelog(history["repo"], "no-such-tag", "HEAD", CHANGELOG)
    with pytest.raises(rf.ChangeLogFault, match="REF_INVALID"):
        rf.check_changelog(history["repo"], "--output=x", "HEAD", CHANGELOG)
    with pytest.raises(rf.ChangeLogFault, match="BASE_NOT_ANCESTOR_OF_CANDIDATE"):
        rf.check_changelog(history["repo"], history["candidate"], "v0.9-synthetic", CHANGELOG)


def test_changelog_line_endings_and_working_tree_do_not_matter(history):
    expected = check(history)
    for newline, bom in (("\r\n", False), ("\n", True), ("\r\n", True)):
        assert check(history, data=changelog_bytes(history["rows"], newline, bom)) == expected
    # Uncommitted working-tree edits and autocrlf settings are invisible to a tree comparison.
    (history["repo"] / "keep.txt").write_bytes(b"k\r\nchanged\r\n")
    (history["repo"] / CHANGELOG).write_bytes(b"garbage")
    git(history["repo"], "config", "core.autocrlf", "true")
    assert check(history) == expected


def test_changelog_cli_exit_codes(history, capsys):
    args = ["changelog", "--repo", str(history["repo"]), "--base", "v0.9-synthetic",
            "--candidate", history["candidate"], "--changelog", CHANGELOG]
    assert rf.main(args) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "PASS"
    assert rf.main(args + ["--require-reviewed"]) == 1
    draft = history["repo"].parent / "draft.csv"
    draft.write_bytes(changelog_bytes(history["rows"][:1]))
    assert rf.main(args + ["--draft-file", str(draft)]) == 1
    assert "UNCOVERED_PATH" in capsys.readouterr().out
    assert rf.main(args[:-1] + ["../x.csv"]) == 2


# ------------------------------------------------------------------ freeze bundle

PROTOCOL_CRLF = b"# Synthetic protocol v1.0\r\n\r\nPlaceholder text only.\r\n"


def sealed_file(tmp_path, name, data):
    path = tmp_path / "sealed" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def plan_a(tmp_path, commit="1" * 40):
    alloc_path, alloc_hash = sealed_file(tmp_path, "a.bin", b"synthetic sealed list\n")
    _, manifest_hash = sealed_file(tmp_path, "m.json", b"{}")
    items = {}
    for item, spec in rp.items("A").items():
        if spec["conceal"] == "required":
            items[item] = {"status": "concealed", "sha256": alloc_hash if item == "allocation" else manifest_hash,
                           "reference": "SEALED-" + item.upper().replace("_", "-")}
        elif spec["required"] or item in {"station_config", "fault_thresholds", "experimenter_scripts"}:
            items[item] = {"status": "files"}
        else:
            items[item] = {"status": "not_applicable", "reason": "Synthetic test: not part of this bundle"}
    items["run_sheets"] = {"status": "not_applicable", "reason": "Synthetic test: covered by trial orders"}
    return {"schema_version": 1, "study": "A", "label": "protocol-a-v1.0", "source_commit": commit,
            "items": items}, alloc_path


def build_bundle(root, plan):
    files = {
        "app_build/experiment.apk": bytes(range(256)) * 64,
        "console_build/console.exe": b"MZ synthetic console",
        "station_config/audio.json": b'{"route": "synthetic"}\n',
        "fault_thresholds/thresholds.csv": b"name,value\nfreeze_trigger_ms,250\n",
        "experimenter_scripts/run-steps.md": b"1. Synthetic step\n",
        "protocol_text/protocol-A-v1.0.md": PROTOCOL_CRLF,
        "change_log/changes-v1.0.csv": changelog_bytes([]),
    }
    for rel, data in files.items():
        if plan["items"][rel.split("/")[0]]["status"] == "files":
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
    return root


def write_plan(tmp_path, plan, name="plan.json"):
    path = tmp_path / name
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


@pytest.fixture
def frozen(tmp_path):
    plan, alloc = plan_a(tmp_path)
    bundle = build_bundle(tmp_path / "bundle", plan)
    summary = rf.write_freeze(bundle, write_plan(tmp_path, plan))
    return {"bundle": bundle, "plan": plan, "alloc": alloc, "summary": summary, "tmp": tmp_path}


def resum(bundle):
    """Test-side forger: rewrite SHA256SUMS to match the current files."""
    lines = []
    for path in sorted(p for p in bundle.rglob("*") if p.is_file() and p.name != rp.SUMS_NAME):
        rel = path.relative_to(bundle).as_posix()
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {rel}\n")
    (bundle / rp.SUMS_NAME).write_bytes("".join(sorted(lines, key=lambda l: l[66:])).encode())


def test_freeze_write_and_independent_verify_pass(frozen):
    summary = frozen["summary"]
    assert summary["result"] == "WRITTEN" and summary["freeze_accepted"] is False
    assert summary["files_hashed"] == 7
    result = rv.verify_bundle(frozen["bundle"], sums_sha256=summary["sha256sums_sha256"],
                              concealed=[("allocation", frozen["alloc"])])
    assert result["result"] == "PASS", result["problems"]
    assert result["files_verified"] == 8  # seven bundle files plus FREEZE.json
    assert result["concealed_recomputed"] == ["allocation"]
    assert "apparatus_manifest" in result["concealed_not_recomputed"]
    assert result["freeze_accepted"] is False and result["signoff"] == "human-required"
    # A third, trivial recomputation in the style of `sha256sum -c`.
    for line in (frozen["bundle"] / rp.SUMS_NAME).read_text(encoding="ascii").splitlines():
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((frozen["bundle"] / rel).read_bytes()).hexdigest() == digest
    record = json.loads((frozen["bundle"] / rp.FREEZE_NAME).read_text(encoding="ascii"))
    assert record["items"]["allocation"]["status"] == "concealed"
    assert record["signoff"]["status"] == "pending"


def test_freeze_never_overwrites(frozen):
    with pytest.raises(rp.PolicyFault, match="FREEZE_OUTPUT_EXISTS"):
        rf.write_freeze(frozen["bundle"], write_plan(frozen["tmp"], frozen["plan"], "again.json"))


@pytest.mark.parametrize("tamper,code", [
    (lambda b: (b / "app_build/experiment.apk").write_bytes(b"replaced"), "HASH_MISMATCH"),
    (lambda b: (b / "station_config/extra.json").write_bytes(b"{}"), "UNLISTED_FILE"),
    (lambda b: (b / "console_build/console.exe").unlink(), "MISSING_FILE"),
    (lambda b: (b / rp.FREEZE_NAME).write_bytes(
        (b / rp.FREEZE_NAME).read_bytes().replace(b"SEALED-ALLOCATION", b"SEALED-OTHER")), "HASH_MISMATCH"),
    (lambda b: (b / "app_build/experiment.apk").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 16384\n"),
     "LFS_POINTER_NOT_MATERIALIZED"),
])
def test_tamper_detected(frozen, tamper, code):
    tamper(frozen["bundle"])
    result = rv.verify_bundle(frozen["bundle"])
    assert result["result"] == "FAIL"
    assert code in codes(result)


def test_consistent_forgery_needs_retained_pin(frozen):
    bundle, pin = frozen["bundle"], frozen["summary"]["sha256sums_sha256"]
    (bundle / "app_build/experiment.apk").write_bytes(b"different build")
    resum(bundle)
    assert rv.verify_bundle(bundle)["result"] == "PASS"  # self-consistent forgery
    assert codes(rv.verify_bundle(bundle, sums_sha256=pin)) == ["SUMS_PIN_MISMATCH"]


def test_forged_coverage_detected(frozen):
    bundle = frozen["bundle"]
    record = json.loads((bundle / rp.FREEZE_NAME).read_text(encoding="ascii"))
    del record["items"]["protocol_text"]
    record["items"]["allocation"] = {"status": "not_applicable", "reason": "Forged reason text"}
    (bundle / rp.FREEZE_NAME).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="ascii")
    resum(bundle)
    result = codes(rv.verify_bundle(bundle))
    assert {"MISSING_ITEM", "ITEM_REQUIRED", "FILE_NOT_IN_ANY_ITEM"} <= set(result)


def test_concealed_recompute_mismatch(frozen):
    wrong, _ = sealed_file(frozen["tmp"], "wrong.bin", b"another list\n")
    result = rv.verify_bundle(frozen["bundle"], concealed=[("allocation", wrong), ("app_build", wrong)])
    assert codes(result) == ["CONCEALED_HASH_MISMATCH", "NOT_A_CONCEALED_ITEM"]


@pytest.mark.parametrize("mutate,code", [
    (lambda p, b: p["items"].update(allocation={"status": "files"}), "CONCEALMENT_REQUIRED"),
    (lambda p, b: p["items"].update(apparatus_manifest={"status": "files"}), "CONCEALMENT_REQUIRED"),
    (lambda p, b: (b / "allocation").mkdir() or (b / "allocation/list.csv").write_bytes(b"x\n"),
     "CONCEALED_OR_NA_CONTENT_PRESENT"),
    (lambda p, b: (b / "speech_commands").mkdir() or (b / "speech_commands/a.wav").write_bytes(b"x"),
     "CONCEALED_OR_NA_CONTENT_PRESENT"),
    (lambda p, b: (b / "experimenter_scripts/codebook.md").write_bytes(b"x\n"), "FORBIDDEN_PATH"),
    (lambda p, b: (b / "experimenter_scripts/Study-Vocabulary.txt").write_bytes(b"x\n"), "FORBIDDEN_PATH"),
    (lambda p, b: (b / "station_config/station.local.json").write_bytes(b"{}"), "FORBIDDEN_PATH"),
    (lambda p, b: (b / "station_config/participant-data").mkdir() or
     (b / "station_config/participant-data/x.txt").write_bytes(b"x\n"), "FORBIDDEN_PATH"),
    (lambda p, b: (b / "fault_thresholds/confirmatory_seed.txt").write_bytes(b"1\n"), "FORBIDDEN_PATH"),
    (lambda p, b: (b / "experimenter_scripts/sheet.csv").write_bytes(b"visit,Participant_ID\nD0,x\n"),
     "PARTICIPANT_FIELD"),
    (lambda p, b: (b / "experimenter_scripts/sheet.tsv").write_bytes(b"\xef\xbb\xbfdyad_id\tv\n1\t2\n"),
     "PARTICIPANT_FIELD"),
    (lambda p, b: (b / "station_config/log.jsonl").write_bytes(b'{"t": 1, "learner_id" : "x"}\n'),
     "PARTICIPANT_FIELD"),
    (lambda p, b: (b / "station_config/token.txt").write_bytes(b"ghp" + b"_" + b"a" * 36), "POSSIBLE_CREDENTIAL"),
    (lambda p, b: (b / "station_config/key.txt").write_bytes(
        b"x" * (1024 * 1024 - 10) + b"-----BEGIN " + b"PRIVATE KEY-----"), "POSSIBLE_CREDENTIAL"),
    (lambda p, b: (b / "notes.txt").write_bytes(b"x"), "UNEXPECTED_ROOT_FILE"),
    (lambda p, b: (b / "extras").mkdir() or (b / "extras/x.txt").write_bytes(b"x"), "UNDECLARED_ITEM"),
    (lambda p, b: (b / "station_config/bad name.json").write_bytes(b"{}"), "UNSAFE_BUNDLE_PATH"),
    (lambda p, b: shutil.rmtree(b / "station_config") or (b / "station_config").mkdir(), "EMPTY_ITEM"),
    (lambda p, b: p["items"].update(protocol_text={"status": "not_applicable", "reason": "Not needed here"}),
     "ITEM_REQUIRED"),
    (lambda p, b: p["items"].update(run_sheets={"status": "not_applicable", "reason": ""}), "REASON_REQUIRED"),
    (lambda p, b: p["items"].pop("change_log"), "MISSING_ITEM"),
    (lambda p, b: p["items"].update(extra={"status": "files"}), "UNDECLARED_ITEM"),
    (lambda p, b: p["items"]["allocation"].update(sha256="A" * 64), "CONCEALED_HASH_INVALID"),
    (lambda p, b: p["items"]["allocation"].update(reference="../private/list"), "CONCEALED_REFERENCE_INVALID"),
    (lambda p, b: p["items"]["allocation"].update(contents="x"), "ITEM_RECORD_INVALID"),
    (lambda p, b: p.update(study="C"), "UNKNOWN_STUDY"),
    (lambda p, b: p.update(source_commit="HEAD"), "SOURCE_COMMIT_INVALID"),
])
def test_writer_refuses_forbidden_or_incomplete_bundles(tmp_path, mutate, code):
    plan, _ = plan_a(tmp_path)
    bundle = build_bundle(tmp_path / "bundle", plan)
    mutate(plan, bundle)
    with pytest.raises(rp.PolicyFault, match="^" + code):
        rf.write_freeze(bundle, write_plan(tmp_path, plan))
    assert not (bundle / rp.SUMS_NAME).exists() and not (bundle / rp.FREEZE_NAME).exists()


def test_verifier_reapplies_content_guard(frozen):
    bundle = frozen["bundle"]
    (bundle / "fault_thresholds/thresholds.csv").write_bytes(b"participant_id,value\nx,1\n")
    resum(bundle)
    assert codes(rv.verify_bundle(bundle)) == ["PARTICIPANT_FIELD"]


def test_plan_json_strictness(tmp_path):
    plan, _ = plan_a(tmp_path)
    bundle = build_bundle(tmp_path / "bundle", plan)
    text = json.dumps(plan)
    (tmp_path / "dup.json").write_text(text[:-1] + ', "label": "x"}', encoding="utf-8")
    with pytest.raises(rp.PolicyFault, match="DUPLICATE_JSON_KEY"):
        rf.write_freeze(bundle, tmp_path / "dup.json")
    (tmp_path / "nan.json").write_text(text.replace('"schema_version": 1', '"schema_version": NaN'),
                                       encoding="utf-8")
    with pytest.raises(rp.PolicyFault, match="NONFINITE_JSON"):
        rf.write_freeze(bundle, tmp_path / "nan.json")


def test_links_refused(tmp_path):
    plan, _ = plan_a(tmp_path)
    bundle = build_bundle(tmp_path / "bundle", plan)
    try:
        os.symlink(bundle / "app_build/experiment.apk", bundle / "station_config/link.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation needs privilege on this platform; Linux CI covers it")
    with pytest.raises(rp.PolicyFault, match="LINK_FORBIDDEN"):
        rf.write_freeze(bundle, write_plan(tmp_path, plan))


# ------------------------------------------------------------------ line endings

def test_writer_output_is_lf_and_deterministic(tmp_path):
    outputs = []
    for name in ("one", "two"):
        plan, _ = plan_a(tmp_path)
        bundle = build_bundle(tmp_path / name, plan)
        rf.write_freeze(bundle, write_plan(tmp_path, plan, name + ".json"))
        outputs.append(((bundle / rp.SUMS_NAME).read_bytes(), (bundle / rp.FREEZE_NAME).read_bytes()))
    assert outputs[0] == outputs[1]
    for data in outputs[0]:
        assert b"\r" not in data and data.endswith(b"\n")
    lines = outputs[0][0].decode("ascii").splitlines()
    assert lines == sorted(lines, key=lambda l: l[66:])
    assert all(re.fullmatch(r"[0-9a-f]{64}  \S+", line) for line in lines)


def test_crlf_bytes_hashed_exactly_and_conversion_detected(frozen):
    bundle = frozen["bundle"]
    sums = {rel: digest for digest, rel in
            (line.split("  ", 1) for line in (bundle / rp.SUMS_NAME).read_text(encoding="ascii").splitlines())}
    assert sums["protocol_text/protocol-A-v1.0.md"] == hashlib.sha256(PROTOCOL_CRLF).hexdigest()
    (bundle / "protocol_text/protocol-A-v1.0.md").write_bytes(PROTOCOL_CRLF.replace(b"\r\n", b"\n"))
    assert codes(rv.verify_bundle(bundle)) == ["HASH_MISMATCH"]


def test_converted_sums_file_refused(frozen, capsys):
    path = frozen["bundle"] / rp.SUMS_NAME
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(rv.VerifyFault, match="SUMS_NOT_LF"):
        rv.verify_bundle(frozen["bundle"])
    assert rv.main(["--bundle", str(frozen["bundle"])]) == 2
    assert "SUMS_NOT_LF" in capsys.readouterr().err


def archive_rule():
    rules = [line for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
             if line.startswith("docs/protocol/v")]
    assert rules == ["docs/protocol/v*/** -text"]
    return rules[0]


@pytest.mark.parametrize("protected", [True, False])
def test_git_checkout_round_trip_with_autocrlf(frozen, tmp_path, protected):
    """With the repository's archive rule a CRLF-converting checkout stays byte-exact."""
    source = tmp_path / "origin"
    source.mkdir()
    git(source, "init", "-q")
    attributes = "* text=auto eol=lf\n" + (archive_rule() + "\n" if protected else "")
    files = {".gitattributes": attributes.encode()}
    for path in frozen["bundle"].rglob("*"):
        if path.is_file():
            files["docs/protocol/v1.0/" + path.relative_to(frozen["bundle"]).as_posix()] = path.read_bytes()
    commit_files(source, files, "synthetic archive")
    clone = tmp_path / "clone"
    subprocess.run(["git", "-c", "core.autocrlf=true", "clone", "-q", str(source), str(clone)],
                   check=True, capture_output=True)
    result = rv.verify_bundle(clone / "docs/protocol/v1.0")
    if protected:
        assert result["result"] == "PASS", result["problems"]
    else:
        assert "HASH_MISMATCH" in codes(result)


# ------------------------------------------------------------------ tags, stations, coverage

def test_tag_must_point_to_source_commit(frozen, history):
    candidate = history["candidate"]
    plan = dict(frozen["plan"], source_commit=candidate)
    bundle = build_bundle(frozen["tmp"] / "tagged", plan)
    rf.write_freeze(bundle, write_plan(frozen["tmp"], plan, "tagged.json"))
    git(history["repo"], "tag", "protocol-a-v1.0", candidate)
    git(history["repo"], "tag", "wrong", history["first"])
    ok = rv.verify_bundle(bundle, repo=history["repo"], tag="protocol-a-v1.0")
    assert ok["result"] == "PASS" and ok["tag_checked"]
    assert codes(rv.verify_bundle(bundle, repo=history["repo"], tag="wrong")) == ["TAG_MISMATCH"]
    assert codes(rv.verify_bundle(bundle, repo=history["repo"], tag="missing")) == ["TAG_MISMATCH"]


def station_record(path, installed, match):
    values = dict.fromkeys(quest_station.FIELDS, "")
    values.update(record_status="observed_pending_operator", station_id="station-x", unit_id="unit-x",
                  installed_apk_sha256=installed, installed_artifact_match=match)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=quest_station.FIELDS)
        writer.writeheader()
        writer.writerow(values)
    return path


def test_station_install_hash_checked_against_archive(frozen):
    app = hashlib.sha256((frozen["bundle"] / "app_build/experiment.apk").read_bytes()).hexdigest()
    good = station_record(frozen["tmp"] / "s1.local.csv", app, "true")
    stale = station_record(frozen["tmp"] / "s2.local.csv", "b" * 64, "true")
    broken = frozen["tmp"] / "s3.local.csv"
    broken.write_text("not,a,record\n", encoding="utf-8")
    result = rv.verify_bundle(frozen["bundle"], station_records=[good, stale, broken])
    assert result["station_records"] == {"checked": 2, "matching": 1}
    assert result["problems"] == [{"code": "STATION_BUILD_NOT_ARCHIVED", "record": 2},
                                  {"code": "STATION_RECORD_INVALID", "record": 3}]
    assert "station-x" not in json.dumps(result)


def test_study_b_bundle_round_trip(tmp_path):
    _, alloc_hash = sealed_file(tmp_path, "b.bin", b"synthetic B list\n")
    items = {}
    for item, spec in rp.items("B").items():
        if item == "allocation":
            items[item] = {"status": "concealed", "sha256": alloc_hash, "reference": "SEALED-B-LIST"}
        elif spec["required"]:
            items[item] = {"status": "files"}
        else:
            items[item] = {"status": "not_applicable", "reason": "Synthetic test: not part of this bundle"}
    plan = {"schema_version": 1, "study": "B", "label": "protocol-b-v1.0", "source_commit": "2" * 40,
            "items": items}
    bundle = tmp_path / "b"
    for rel in ("app_build/experiment.apk", "protocol_text/protocol-B-v1.0.md"):
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rel).write_bytes(rel.encode())
    plan_path = write_plan(tmp_path, plan)
    assert rf.main(["write", "--bundle", str(bundle), "--plan", str(plan_path)]) == 0
    assert rf.main(["write", "--bundle", str(bundle), "--plan", str(plan_path)]) == 2  # never overwrites
    assert rv.verify_bundle(bundle)["result"] == "PASS"
    assert rv.main(["--bundle", str(bundle), "--concealed", "allocation=" + str(tmp_path / "sealed/b.bin")]) == 0


def test_declared_coverage_matches_issue_contents_and_docs():
    a, b = rp.items("A"), rp.items("B")
    for item in ("app_build", "console_build", "station_config", "isaac_scene", "robot_asset", "reset_snapshot",
                 "curriculum_tables", "trial_orders", "run_sheets", "allocation", "lesson_test_scripts",
                 "experimenter_scripts", "speech_commands", "fault_thresholds", "apparatus_manifest",
                 "sample_size_decision", "g4_freeze_record"):
        assert item in a, item
    for item in ("app_build", "screening_presets", "menu_timings", "session_scripts", "presentation_ledger_format",
                 "visit_schedule", "allocation", "bank_generation_config", "failure_rules"):
        assert item in b, item
    assert a["allocation"] == dict(a["allocation"], conceal="required", required=True)
    assert b["allocation"] == dict(b["allocation"], conceal="required", required=True)
    docs = (ROOT / "docs/release/README.md").read_text(encoding="utf-8")
    for study, declared in (("A", a), ("B", b)):
        for item, spec in declared.items():
            assert re.search(rf"\| {study} \| `{item}` \|[^\n]*\| {spec['conceal']} \| "
                             rf"{'yes' if spec['required'] else 'no'} \|", docs), (study, item)
    assert rp.COVERAGE_VERSION in docs

"""Procedure-requirement traceability for the experiment application.

The catalogue `docs/requirements/requirements.json` lists numbered requirements
(`REQ-<AREA>-<NN>`) with neutral descriptions. Each requirement is verified in one
of three ways:

- ``auto``: at least one automated test. A test carries an ID either through the
  catalogue's ``tests`` list (existing tests, referenced by file and name) or by
  tagging itself: NUnit ``[Category("REQ_TEST_04")]`` in Unity tests (Unity's NUnit
  rejects ``-`` in category names, so Unity tags use underscores; an optional
  ``Property("Requirement", "REQ-TEST-04")`` may repeat the hyphenated ID), or the ID
  in a pytest test's docstring or name (``test_req_test_04_...``).
- ``measure``: a measurement procedure in `docs/requirements/measurements.md`
  (heading ``### MEAS-<ID>``) and the gate it belongs to.
- ``process``: a human/administrative check with its gate.

An ``auto`` requirement without tests must name the tracking issue (``issue``).

Commands::

    python tools/requirements_trace.py check            # static check, exit 1 on errors
    python tools/requirements_trace.py index            # rewrite docs/requirements/README.md
    python tools/requirements_trace.py index --check    # fail if README.md is stale
    python tools/requirements_trace.py report --nunit editmode.xml --junit pytest.xml [--json out.json]

`report` maps NUnit and JUnit result files to requirements: ``pass`` when every
mapped test that ran passed, ``fail`` when any failed, ``not-run`` when none ran.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = Path("docs/requirements/requirements.json")
MEASUREMENTS = Path("docs/requirements/measurements.md")
INDEX = Path("docs/requirements/README.md")

ID_RE = re.compile(r"REQ-[A-Z]+-[0-9]{2}")
NAME_ID_RE = re.compile(r"req_([a-z]+)_([0-9]{2})")
CS_CATEGORY_RE = re.compile(r'Category\(\s*"REQ_([A-Z]+)_([0-9]{2})"\s*\)')
CS_PROPERTY_RE = re.compile(r'Property\(\s*"Requirement"\s*,\s*"(REQ-[A-Z]+-[0-9]{2})"\s*\)')
CS_BAD_CATEGORY_RE = re.compile(r'Category\(\s*"([^"]*[-,!+][^"]*)"\s*\)')
CS_METHOD_RE = re.compile(r"public\s+(?:async\s+)?(?:void|IEnumerator|Task)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
CS_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)")
CS_NAMESPACE_RE = re.compile(r"\bnamespace\s+([A-Za-z_][A-Za-z0-9_.]*)")
VERIFICATIONS = {"auto", "measure", "process"}
GATES = {"G2", "G3", "INSTALL", "IRB", "PILOT", "FREEZE"}
OWNERS = {"app", "console", "schedules", "analysis", "tools", "generation", "banks", "station", "study-team"}
UNITY_ROOT = Path("unity/Assets")
PY_TEST_ROOTS = (Path("tests"), Path("ops/console/tests"))


@dataclass(frozen=True)
class TestRef:
    path: str
    name: str
    kind: str  # "nunit" | "pytest"
    tagged: bool = False
    suite: str = ""  # NUnit fixture fullname or pytest module stem


@dataclass
class Trace:
    requirements: list[dict]
    tests: dict[str, list[TestRef]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def load_catalogue(root: Path = ROOT) -> list[dict]:
    data = json.loads((root / CATALOGUE).read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("requirements"), list):
        raise ValueError("catalogue must have version 1 and a requirements list")
    return data["requirements"]


CS_LITERAL_RE = re.compile(r'@"(?:[^"]|"")*"|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)\'')


def _cs_tests(path: Path, root: Path) -> list[tuple[str, str, list[str]]]:
    """Return (suite, method, [REQ ids]) for each test method in a C# file.

    The suite is the namespace plus the top-level class (the NUnit fixture); helper
    classes nested inside a fixture do not change it.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    ns = CS_NAMESPACE_RE.search(text)
    namespace = ns.group(1) if ns else ""
    file_scoped = bool(ns and text[ns.end():].lstrip().startswith(";"))
    fixture_depth = 0 if file_scoped or not ns else 1
    out = []
    pending: list[str] = []
    current_class = ""
    depth = 0
    for line in text.splitlines():
        code = CS_LITERAL_RE.sub('""', line.split("//", 1)[0] if '"' not in line else line)
        cls = CS_CLASS_RE.search(code)
        before = code[: cls.start()] if cls else ""
        at = depth + before.count("{") - before.count("}")
        if cls and "(" not in before and at == fixture_depth:
            current_class = cls.group(1)
        depth += code.count("{") - code.count("}")
        pending.extend(f"REQ-{a}-{n}" for a, n in CS_CATEGORY_RE.findall(line))
        pending.extend(CS_PROPERTY_RE.findall(line))
        method = CS_METHOD_RE.search(line)
        if method:
            suite = f"{namespace}.{current_class}" if namespace else current_class
            out.append((suite, method.group(1), sorted(set(pending))))
            pending = []
    return out


def _py_tests(path: Path) -> list[tuple[str, list[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            ids = set(ID_RE.findall(ast.get_docstring(node) or ""))
            ids.update(f"REQ-{a.upper()}-{n}" for a, n in NAME_ID_RE.findall(node.name))
            out.append((node.name, sorted(ids)))
    return out


def scan_tagged(root: Path = ROOT) -> dict[str, list[TestRef]]:
    tagged: dict[str, list[TestRef]] = {}
    unity = root / UNITY_ROOT
    if unity.is_dir():
        for path in sorted(unity.rglob("*.cs")):
            rel = path.relative_to(root).as_posix()
            if "/ThirdParty/" in rel or "/Tests/" not in rel:
                continue
            for suite, method, ids in _cs_tests(path, root):
                for i in ids:
                    tagged.setdefault(i, []).append(TestRef(rel, method, "nunit", True, suite))
    for base in PY_TEST_ROOTS:
        if not (root / base).is_dir():
            continue
        for path in sorted((root / base).rglob("test_*.py")):
            rel = path.relative_to(root).as_posix()
            for name, ids in _py_tests(path):
                for i in ids:
                    tagged.setdefault(i, []).append(TestRef(rel, name, "pytest", True, path.stem))
    return tagged


def bad_categories(root: Path = ROOT) -> list[str]:
    """NUnit category names Unity's test runner refuses (it rejects - , ! and +)."""
    found = []
    unity = root / UNITY_ROOT
    if not unity.is_dir():
        return found
    for path in sorted(unity.rglob("*.cs")):
        rel = path.relative_to(root).as_posix()
        if "/ThirdParty/" in rel:
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for name in CS_BAD_CATEGORY_RE.findall(line):
                found.append(f"{rel}:{no}: NUnit category {name!r} contains - , ! or +; use REQ_X_NN")
    return found


def _test_exists(root: Path, ref: dict) -> bool:
    path = root / ref["path"]
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    name = re.escape(ref["name"])
    if path.suffix == ".cs":
        return re.search(rf"(?:void|IEnumerator|Task)\s+{name}\s*\(", text) is not None
    return re.search(rf"def\s+{name}\s*\(", text) is not None


def measurement_ids(root: Path = ROOT) -> set[str]:
    path = root / MEASUREMENTS
    if not path.is_file():
        return set()
    return set(re.findall(r"^###\s+MEAS-(REQ-[A-Z]+-[0-9]{2})\b", path.read_text(encoding="utf-8"), re.M))


def build(root: Path = ROOT) -> Trace:
    reqs = load_catalogue(root)
    trace = Trace(reqs)
    seen: set[str] = set()
    known = {r.get("id") for r in reqs}
    tagged = scan_tagged(root)
    measured = measurement_ids(root)
    trace.errors.extend(bad_categories(root))
    for unknown in sorted(set(tagged) - known):
        where = ", ".join(f"{t.path}::{t.name}" for t in tagged[unknown])
        trace.errors.append(f"{unknown}: tagged in tests but not in the catalogue ({where})")
    for req in reqs:
        rid = req.get("id", "")
        if not ID_RE.fullmatch(rid):
            trace.errors.append(f"{rid!r}: malformed requirement id")
            continue
        if rid in seen:
            trace.errors.append(f"{rid}: duplicate id")
        seen.add(rid)
        if req.get("area") != rid.split("-")[1]:
            trace.errors.append(f"{rid}: area must be {rid.split('-')[1]}")
        if not str(req.get("text", "")).strip():
            trace.errors.append(f"{rid}: empty text")
        if req.get("owner") not in OWNERS:
            trace.errors.append(f"{rid}: owner must be one of {sorted(OWNERS)}")
        ver = req.get("verification")
        if ver not in VERIFICATIONS:
            trace.errors.append(f"{rid}: verification must be one of {sorted(VERIFICATIONS)}")
        gate = req.get("gate")
        if gate is not None and gate not in GATES:
            trace.errors.append(f"{rid}: gate must be one of {sorted(GATES)}")
        refs: list[TestRef] = []
        for ref in req.get("tests", []):
            if not _test_exists(root, ref):
                trace.errors.append(f"{rid}: listed test not found: {ref.get('path')}::{ref.get('name')}")
                continue
            kind = "nunit" if ref["path"].endswith(".cs") else "pytest"
            suite = ref.get("suite") or Path(ref["path"]).stem
            refs.append(TestRef(ref["path"], ref["name"], kind, False, suite))
        refs.extend(tagged.get(rid, []))
        trace.tests[rid] = refs
        for code in req.get("code", []):
            if not (root / code).exists():
                trace.errors.append(f"{rid}: code path not found: {code}")
        if ver == "auto" and not refs and not req.get("issue"):
            trace.errors.append(f"{rid}: automated requirement has no test and no tracking issue")
        if ver == "measure":
            if gate is None:
                trace.errors.append(f"{rid}: measurement requirement needs a gate")
            if rid not in measured:
                trace.errors.append(f"{rid}: no '### MEAS-{rid}' procedure in {MEASUREMENTS}")
        if ver == "process" and gate is None:
            trace.errors.append(f"{rid}: process requirement needs a gate")
    return trace


def status(req: dict, refs: list[TestRef]) -> str:
    ver = req["verification"]
    if ver == "measure":
        return f"measurement ({req['gate']})"
    if ver == "process":
        return f"process ({req['gate']})"
    if refs:
        return "tested" if not req.get("issue") else f"tested; open gap #{req['issue']}"
    return f"open gap #{req['issue']}"


def render_index(trace: Trace) -> str:
    lines = [
        "# Requirement index",
        "",
        "Generated by `python tools/requirements_trace.py index`; edit",
        "`requirements.json` and rerun. Descriptions are neutral engineering",
        "requirements for the experiment application and its tooling. Measurement",
        "procedures are in [measurements.md](measurements.md).",
        "",
        "Unity tests carry an ID as an NUnit category with underscores (`REQ_TEST_04`;",
        "run one requirement with `-testCategory REQ_TEST_04`). Python tests carry the",
        "ID in their docstring or name. \"Open gap\" means the requirement has tests that",
        "state it (marked as known gaps where the code does not yet satisfy it) and an",
        "open issue tracking the fix.",
        "",
    ]
    areas: dict[str, list[dict]] = {}
    for req in trace.requirements:
        areas.setdefault(req["area"], []).append(req)
    counts: dict[str, int] = {}
    for req in trace.requirements:
        key = status(req, trace.tests.get(req["id"], [])).split(" ")[0].rstrip(";")
        if req["verification"] == "auto" and req.get("issue"):
            key = "tested, open gap" if trace.tests.get(req["id"]) else "untested, open gap"
        counts[key] = counts.get(key, 0) + 1
    lines.append("| Status | Requirements |")
    lines.append("| --- | ---: |")
    for key in sorted(counts):
        lines.append(f"| {key} | {counts[key]} |")
    lines.append(f"| total | {len(trace.requirements)} |")
    lines.append("")
    for area in sorted(areas):
        lines.append(f"## {area}")
        lines.append("")
        lines.append("| ID | Requirement | Owner | Verification | Tests |")
        lines.append("| --- | --- | --- | --- | ---: |")
        for req in areas[area]:
            refs = trace.tests.get(req["id"], [])
            text = req["text"].replace("|", "\\|")
            lines.append(f"| {req['id']} | {text} | {req['owner']} | {status(req, refs)} | {len(refs)} |")
        lines.append("")
    return "\n".join(lines)


def _nunit_results(paths: list[str]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for p in paths:
        for case in ET.parse(p).getroot().iter("test-case"):
            full = case.get("fullname", "")
            name = case.get("methodname") or full.rsplit(".", 1)[-1]
            suite = full.rsplit(".", 1)[0] if "." in full else ""
            out[(suite, name.split("(")[0])] = case.get("result", "")
    return out


def _junit_results(paths: list[str]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for p in paths:
        for case in ET.parse(p).getroot().iter("testcase"):
            stem = case.get("classname", "").split(".")
            module = next((s for s in reversed(stem) if s.startswith("test_")), stem[-1] if stem else "")
            name = case.get("name", "").split("[")[0]
            if case.find("failure") is not None or case.find("error") is not None:
                result = "Failed"
            elif case.find("skipped") is not None:
                result = "Skipped"
            else:
                result = "Passed"
            prev = out.get((module, name))
            out[(module, name)] = "Failed" if "Failed" in (prev, result) else result
    return out


def report(trace: Trace, nunit: list[str], junit: list[str]) -> list[dict]:
    nres = _nunit_results(nunit)
    jres = _junit_results(junit)
    rows = []
    for req in trace.requirements:
        refs = trace.tests.get(req["id"], [])
        results = []
        for ref in refs:
            if ref.kind == "nunit":
                hits = [v for (suite, name), v in nres.items() if name == ref.name and (not ref.tagged or suite == ref.suite or suite.endswith("." + ref.suite.rsplit(".", 1)[-1]))]
            else:
                hits = [v for (module, name), v in jres.items() if name == ref.name and module == ref.suite]
            results.extend(hits)
        if req["verification"] != "auto":
            outcome = req["verification"]
        elif not refs:
            outcome = "missing"
        elif any(r == "Failed" for r in results):
            outcome = "fail"
        elif any(r == "Passed" for r in results):
            outcome = "pass"
        else:
            outcome = "not-run"
        rows.append({"id": req["id"], "outcome": outcome, "tests": len(refs), "results": results, "issue": req.get("issue")})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    idx = sub.add_parser("index")
    idx.add_argument("--check", action="store_true", help="fail if the index is stale")
    rep = sub.add_parser("report")
    rep.add_argument("--nunit", action="append", default=[])
    rep.add_argument("--junit", action="append", default=[])
    rep.add_argument("--json")
    args = parser.parse_args(argv)
    trace = build(ROOT)
    if args.cmd == "check":
        for e in trace.errors:
            print(e, file=sys.stderr)
        tested = sum(1 for r in trace.requirements if trace.tests.get(r["id"]))
        print(f"{len(trace.requirements)} requirements; {tested} with tests; {len(trace.errors)} errors")
        return 1 if trace.errors else 0
    if args.cmd == "index":
        text = render_index(trace) + "\n"
        target = ROOT / INDEX
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                print(f"{INDEX} is stale; run: python tools/requirements_trace.py index", file=sys.stderr)
                return 1
            return 0
        target.write_text(text, encoding="utf-8")
        return 0
    rows = report(trace, args.nunit, args.junit)
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["outcome"]] = summary.get(row["outcome"], 0) + 1
    for row in rows:
        if row["outcome"] in {"fail", "missing", "not-run"}:
            print(f"{row['id']}: {row['outcome']}" + (f" (#{row['issue']})" if row.get("issue") else ""))
    print(json.dumps(summary, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

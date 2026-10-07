"""Package skeleton: every module imports and is documented in an analysis guide."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import numpy as np
import pytest

import av_analysis
from av_analysis import seeds, vocab
from av_analysis.fileio import (
    CsvFormatError,
    canonical_json_bytes,
    csv_bytes,
    file_entry,
    json_bytes,
    parse_csv,
    sha256_bytes,
    sha256_file,
    write_bytes,
)

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "analysis" / "docs"
ARCHITECTURE = DOCS / "architecture.md"
INTERFACES = ROOT / "docs" / "interfaces" / "analysis.md"
MODULES = sorted(m.name for m in pkgutil.iter_modules(av_analysis.__path__) if m.name != "__main__")
SKELETON_MODULES = (
    "_paths",
    "cli",
    "codes",
    "derive",
    "derived",
    "estimators",
    "fileio",
    "glmm",
    "ledger",
    "loaders",
    "masking",
    "missingness",
    "monitoring",
    "paths",
    "pipeline",
    "rbridge",
    "reconcile",
    "references",
    "report",
    "schemas",
    "scoring",
    "seeds",
    "simulate",
    "synthetic_logs",
    "templates",
    "unmask",
    "vocab",
    "windows",
)


def _guides() -> str:
    """Text of every analysis guide: architecture.md and the issues' own guides
    (reconciliation.md, pipeline.md, monitoring.md), so a module an issue adds is
    documented in its own guide without editing the shared architecture table."""
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(DOCS.glob("*.md")))


@pytest.mark.parametrize("name", MODULES)
def test_module_imports_and_is_documented(name):
    module = importlib.import_module(f"av_analysis.{name}")
    assert module.__doc__ and module.__doc__.strip()
    assert f"`{name}`" in _guides(), f"document `{name}` in an analysis/docs/*.md guide"


def test_skeleton_modules_are_in_the_architecture_table():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    for name in SKELETON_MODULES:
        assert f"| `{name}` |" in text, name
    assert set(SKELETON_MODULES) <= set(MODULES)


def test_interface_document_has_a_section_per_issue():
    text = INTERFACES.read_text(encoding="utf-8")
    for heading in (
        "## Reconciliation (#33)",
        "## Analysis pipeline (#34)",
        "## Integrity dashboard (#35)",
    ):
        assert heading in text


def test_docs_hold_no_absolute_local_paths():
    for path in (*sorted(DOCS.glob("*.md")), INTERFACES, ROOT / "analysis" / "README.md"):
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text and "C:\\" not in text


def test_vocabularies_are_unique():
    for values in (
        vocab.PLAYBACK_STATUS,
        vocab.AUDIBLE_STATUS,
        vocab.RESPONSE_CODES,
        vocab.FAULT_TYPES,
        vocab.DEVIATION_CATEGORIES,
        vocab.VISIT_STATES,
        vocab.TIMINGS,
        vocab.MISSING_REASONS,
        vocab.ROW_SOURCES,
    ):
        assert len(set(values)) == len(values)
    assert set(vocab.FAULT_TITLES) == set(vocab.FAULT_TYPES)
    assert vocab.BATTERIES == ("pre_old", "trained", "novel", "atomic", "validity")
    assert vocab.ALL_VISITS == ("D0", "D7", "V1", "V2", "V3", "W1", "W4")


def test_seeds_are_deterministic_and_labelled():
    a = seeds.rng("DEMO-x", "A-bootstrap").integers(0, 2**32, size=4)
    b = seeds.rng("DEMO-x", "A-bootstrap").integers(0, 2**32, size=4)
    c = seeds.rng("DEMO-x", "B-bootstrap").integers(0, 2**32, size=4)
    assert np.array_equal(a, b) and not np.array_equal(a, c)
    assert seeds.seed_int("x") == seeds.seed_int("x") < 2**128
    assert seeds.is_demo("DEMO-1") and not seeds.is_demo("A-primary-v1")
    for bad in ((), ("a|b",), ("",)):
        with pytest.raises(ValueError):
            seeds.seed_int(*bad)


def test_fileio_canonical_bytes(tmp_path):
    assert json_bytes({"b": 1, "a": "é"}) == '{\n  "a": "é",\n  "b": 1\n}\n'.encode()
    assert canonical_json_bytes({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'
    data = csv_bytes(["a", "b"], [["1", "x,y"], ["2", ""]])
    assert data == b'a,b\n1,"x,y"\n2,\n'
    with pytest.raises(CsvFormatError):
        csv_bytes(["a"], [["1", "2"]])
    assert parse_csv(b"\xef\xbb\xbfa,b\r\n1,2\r\n") == (("a", "b"), [("1", "2")])
    for bad in (b"", b"a,a\n", b"a,\n", b"a,b\n1\n", b"\xff", b'a\n"x'):
        with pytest.raises(CsvFormatError):
            parse_csv(bad)
    path = tmp_path / "d" / "f.csv"
    write_bytes(path, data)
    assert path.read_bytes() == data and not (tmp_path / "d" / ".f.csv.tmp").exists()
    assert sha256_file(path) == sha256_bytes(data)
    assert file_entry(path, "d/f.csv") == {
        "path": "d/f.csv",
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }

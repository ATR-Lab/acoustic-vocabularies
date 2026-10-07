"""Declared v1.0 freeze coverage and the public-content guard for release bundles.

Shared policy only: the freeze writer (`release_freeze.py`) and the independent
verifier (`release_freeze_verify.py`) both read these declarations, but each
walks, parses and hashes bundle files with its own code.

The item lists restate the freeze contents named in issues #86 (Study A) and #87
(Study B). They are engineering placeholders for the Common procedures section 9
list, which lives outside this repository; reconcile them with that text before
a real freeze. No methodology text is copied here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import repo_guard  # noqa: E402  (single source for forbidden paths and key patterns)

COVERAGE_VERSION = "o6.4-proposed-1"
SUMS_NAME = "SHA256SUMS"
FREEZE_NAME = "FREEZE.json"

# (item_id, description, conceal, required)
# conceal: "required" -> contents never enter a bundle; only an externally computed
#          SHA-256 and an opaque reference are recorded.
#          "optional" -> bundle files, or concealed when the owner decides the
#          contents are not public.
# required: True -> the item may not be declared not_applicable.
STUDY_ITEMS = {
    "A": (
        ("app_build", "App v1.0 build(s) for the chosen topology", "optional", True),
        ("console_build", "Operator console build", "optional", True),
        ("station_config", "Station config files (station identifiers stay in *.local.* files)",
         "optional", False),
        ("isaac_scene", "Isaac scene USD", "optional", False),
        ("robot_asset", "Robot asset", "optional", False),
        ("reset_snapshot", "Reset snapshot", "optional", False),
        ("curriculum_tables", "Curriculum, label permutation and holdout tables", "required", False),
        ("trial_orders", "Lesson and trial orders with stored seeds", "required", False),
        ("run_sheets", "Run sheets", "required", False),
        ("allocation", "Study A allocation lists (hash only)", "required", True),
        ("lesson_test_scripts", "Lesson and test scripts", "optional", False),
        ("experimenter_scripts", "Experimenter scripts", "optional", False),
        ("speech_commands", "Speech-command recordings (#71)", "optional", False),
        ("fault_thresholds", "Fault thresholds, onset calibration and freeze trigger value",
         "optional", False),
        ("apparatus_manifest", "Apparatus manifest v1.0 (private manifest; hash only)", "required", True),
        ("sample_size_decision", "Study A sample-size decision record (O6.4.1)", "optional", False),
        ("g4_freeze_record", "G4 freeze record (#25)", "optional", False),
        ("protocol_text", "Protocol v1.0 text", "optional", True),
        ("change_log", "v1.0 change log (#85)", "optional", True),
    ),
    "B": (
        ("app_build", "Study A v1.0 build or documented minor-version build", "optional", True),
        ("minor_version_record", "Minor-version record with change log and regression evidence",
         "optional", False),
        ("screening_presets", "Screening procedure, three profile presets and calibration example",
         "optional", False),
        ("menu_timings", "Profile and atom menu timings", "optional", False),
        ("session_scripts", "Active and yoked scripts", "optional", False),
        ("presentation_ledger_format", "Presentation-ledger format", "optional", False),
        ("visit_schedule", "Atom growth, visit windows and assessment counts", "optional", False),
        ("allocation", "Study B allocation list (hash only)", "required", True),
        ("bank_generation_config", "Bank generation configuration", "optional", False),
        ("failure_rules", "Absence, withdrawal and failure rules", "optional", False),
        ("sample_size_decision", "Study B sample-size decision record (O6.4.3)", "optional", False),
        ("protocol_text", "Protocol v1.0 text", "optional", True),
    ),
}

ITEM_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")

# Beyond repo_guard.forbidden_path: names that suggest study or participant material.
FORBIDDEN_NAME = re.compile(
    r"(?:allocation|participant|codebook|vocabular|learner[_-]?package|candidate[_-]?bank|"
    r"confirmatory[_-]?seed|master[_-]?seed|consent|roster|enrol)")
# Header columns that identify participants or assignments in tabular files.
FORBIDDEN_COLUMNS = {"participant_id", "participant", "learner_id", "dyad_id", "subject_id", "pid",
                     "pseudonym", "allocation", "assignment", "assigned_arm", "assigned_condition",
                     "randomization", "consent_id"}
FORBIDDEN_JSON_KEY = re.compile(rb'"(?:participant|learner|dyad|subject)_id"\s*:')
TABULAR = {".csv", ".tsv"}
JSONISH = {".json", ".jsonl"}
SCAN_OVERLAP = 512


class PolicyFault(ValueError):
    """Bounded refusal code; the offending bundle-relative path is attached when public-safe."""

    def __init__(self, code, path=None):
        super().__init__(code if path is None else f"{code}: {path}")
        self.code = code
        self.path = path


def items(study):
    if study not in STUDY_ITEMS:
        raise PolicyFault("UNKNOWN_STUDY")
    return {item: {"description": text, "conceal": conceal, "required": required}
            for item, text, conceal, required in STUDY_ITEMS[study]}


def check_relpath(rel):
    """Bundle-relative POSIX path: item directory plus safe ASCII segments, no traversal."""
    parts = rel.split("/")
    if len(parts) < 2 or len(rel) > 512 or not all(SEGMENT.fullmatch(p) for p in parts) \
            or any(p in {".", ".."} for p in parts):
        raise PolicyFault("UNSAFE_BUNDLE_PATH", rel if all(SEGMENT.fullmatch(p) for p in parts) else None)
    return parts


def check_name(rel):
    """Refuse paths the public-repository policy forbids, by name."""
    lowered = rel.lower()
    if repo_guard.forbidden_path(lowered) or FORBIDDEN_NAME.search(PurePosixPath(lowered).name):
        raise PolicyFault("FORBIDDEN_PATH", rel)
    if any(FORBIDDEN_NAME.search(part) for part in PurePosixPath(lowered).parts[1:-1]):
        raise PolicyFault("FORBIDDEN_PATH", rel)


class ContentScanner:
    """Streaming content guard: credential patterns, participant columns and keys."""

    def __init__(self, rel):
        self.rel = rel
        self.suffix = PurePosixPath(rel.lower()).suffix
        self.tail = b""
        self.first = b""
        self.done_header = False

    def feed(self, chunk):
        window = self.tail + chunk
        if any(p.search(window) for p in repo_guard.SECRET_PATTERNS):
            raise PolicyFault("POSSIBLE_CREDENTIAL", self.rel)
        if self.suffix in JSONISH and FORBIDDEN_JSON_KEY.search(window):
            raise PolicyFault("PARTICIPANT_FIELD", self.rel)
        if self.suffix in TABULAR and not self.done_header:
            self.first += chunk
            if b"\n" in self.first or len(self.first) > 65536:
                self._header()
        self.tail = window[-SCAN_OVERLAP:]

    def finish(self):
        if self.suffix in TABULAR and not self.done_header:
            self._header()

    def _header(self):
        self.done_header = True
        line = self.first.split(b"\n", 1)[0].decode("utf-8", "replace").lstrip("﻿")
        cells = re.split(r"[,\t;]", line)
        names = {c.strip().strip('"').strip().lower() for c in cells}
        if names & FORBIDDEN_COLUMNS:
            raise PolicyFault("PARTICIPANT_FIELD", self.rel)


def check_item(study, item, entry, *, recorded):
    """Validate one item declaration. Plans give {"status": "files"}; FREEZE.json
    (recorded=True) also lists the item's bundle files."""
    spec = items(study).get(item)
    if spec is None:
        raise PolicyFault("UNDECLARED_ITEM", item if ITEM_ID.fullmatch(item) else None)
    if not isinstance(entry, dict) or not isinstance(entry.get("status"), str):
        raise PolicyFault("ITEM_RECORD_INVALID", item)
    status = entry["status"]
    allowed = {"files": {"status", "files"} if recorded else {"status"},
               "concealed": {"status", "sha256", "reference"},
               "not_applicable": {"status", "reason"}}
    if status not in allowed:
        raise PolicyFault("ITEM_STATUS_INVALID", item)
    if set(entry) != allowed[status]:
        raise PolicyFault("ITEM_RECORD_INVALID", item)
    if status == "files" and spec["conceal"] == "required":
        raise PolicyFault("CONCEALMENT_REQUIRED", item)
    if status == "not_applicable":
        if spec["required"]:
            raise PolicyFault("ITEM_REQUIRED", item)
        reason = entry["reason"]
        if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500 \
                or any(ord(c) < 32 for c in reason):
            raise PolicyFault("REASON_REQUIRED", item)
    if status == "concealed":
        if not isinstance(entry["sha256"], str) or not HEX64.fullmatch(entry["sha256"]):
            raise PolicyFault("CONCEALED_HASH_INVALID", item)
        if not isinstance(entry["reference"], str) or not REFERENCE.fullmatch(entry["reference"]):
            raise PolicyFault("CONCEALED_REFERENCE_INVALID", item)
    return status

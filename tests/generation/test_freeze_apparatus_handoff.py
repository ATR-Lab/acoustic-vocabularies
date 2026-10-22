"""G4 freeze manifest (#25) -> apparatus G4 handoff -> apparatus collector (#83).

Runs the converter and collector of `tools/apparatus_manifest.py` on a draft that
`freeze.draft_manifest` builds from the running code, with one synthetic recorded value.
The draft stays a draft: nothing here is a G4 freeze, a sign-off or an approval.
`tests/test_apparatus_manifest.py` covers the same chain on the committed draft.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from av_generation import constants as C
from av_generation import freeze
from av_generation.jsonio import document_text

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "apparatus_manifest", ROOT / "tools" / "apparatus_manifest.py"
)
assert _SPEC is not None and _SPEC.loader is not None
collector = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(collector)

PRECISION = freeze.FreezeValue("bfloat16", "SYNTHETIC test value (#16 plan), not a G4 record")


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_built_draft_converts_and_the_collector_accepts_it(tmp_path):
    manifest = freeze.draft_manifest({"runtime.precision": PRECISION})
    assert manifest["status"] == "draft"
    private = tmp_path / ".local"
    private.mkdir()
    src = private / freeze.DRAFT_NAME
    src.write_text(document_text(manifest), encoding="utf-8", newline="\n")
    out = private / "g4-handoff.json"
    argv = ["g4-handoff", "--freeze", str(src), "--freeze-sha256", _sha256(src), "--output"]
    assert collector.main([*argv, str(out)]) == 0

    handoff = json.loads(out.read_bytes())
    fields = handoff["fields"]
    assert handoff["freeze_status"] == "draft"
    assert {*freeze.APPARATUS_FIELDS, "model_id_provisional"} == set(collector.GENERATION_FIELDS)
    assert fields["model_id_provisional"] == C.MODEL_ID
    assert fields["model_revision"] == C.MODEL_REVISION
    assert fields["runtime_precision"] == "bfloat16"
    for name, value in manifest["apparatus"].items():
        if value is None:
            assert fields[name]["status"] == "pending"
        else:
            assert fields[name] == value

    example = ROOT / "apparatus" / "examples" / "apparatus-manifest-input.example.json"
    config = json.loads(example.read_text(encoding="utf-8"))
    config["g4_freeze"] = {"status": "recorded", "path": out.name, "expected_sha256": _sha256(out)}
    data = collector.canonical(config)
    (private / "input.json").write_bytes(data)
    result = collector.collect(str(private / "input.json"), hashlib.sha256(data).hexdigest())
    copied = result["fields"]
    assert copied["model_id_provisional"]["value"] == C.MODEL_ID
    assert copied["model_id_provisional"]["source"] == "g4-draft:" + _sha256(out)
    assert copied["runtime_precision"]["value"] == "bfloat16"
    assert copied["fallback_bank_hash"]["status"] == "pending"
    assert collector.public_summary(result)["g4_approval_verified"] is False


def test_converter_refuses_what_the_freeze_checker_refuses():
    manifest = freeze.draft_manifest()
    assert collector.freeze_to_handoff(manifest, "0" * 64, "draft")["freeze_status"] == "draft"
    item = next(i for i in manifest["items"] if i["key"] == "renderer.recipe_schema_hash")
    item["value"] = "6" * 64
    assert freeze.manifest_problems(manifest)
    with pytest.raises(collector.ManifestFault, match="FREEZE_ITEM_HASH_MISMATCH"):
        collector.freeze_to_handoff(manifest, "0" * 64, "tampered")
    item["sha256"] = item["value"]
    assert freeze.manifest_problems(manifest)
    with pytest.raises(collector.ManifestFault, match="FREEZE_APPARATUS_MISMATCH"):
        collector.freeze_to_handoff(manifest, "0" * 64, "tampered")

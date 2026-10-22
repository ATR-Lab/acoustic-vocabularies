"""Qualified #26 bank -> #13 dyad package handoff (`av_banks.handoff`, #70 checklist 6).

The bank is the session's seeded synthetic DEMO bank (`built_bank`: the real builder with
the scripted model). Tamper cases run on copies; nothing here is study material.
"""

import json
import shutil

import pytest
from av_generation.bank_manifest import BANK_MANIFEST_FORMAT
from av_sound.dyad_bank import QUALIFIED_BANK_FORMAT
from av_sound.package import build_dyad_package, load_package

from av_banks.amend import amend_bank
from av_banks.handoff import HandoffError, qualified_dyad_bank
from av_banks.layout import BankLayout
from av_banks.manifest import read_manifest, to_dyad_bank


@pytest.fixture
def bank_copy(built_bank, tmp_path):
    target = tmp_path / built_bank.bank_id
    shutil.copytree(built_bank.bank_dir, target)
    return BankLayout(target)


def test_conversion_names_the_manifest_and_every_wav(built_bank):
    assert QUALIFIED_BANK_FORMAT == BANK_MANIFEST_FORMAT
    bank = to_dyad_bank(built_bank.manifest)
    assert bank.qualified and bank.handoff.bank_sha256 == built_bank.bank_sha256
    assert bank.package_bank() == {
        "format": BANK_MANIFEST_FORMAT,
        "format_version": 1,
        "bank_sha256": built_bank.bank_sha256,
    }
    cell = built_bank.manifest.cell("P3", "K-a2")
    assert [o.file_sha256 for o in bank.cells[("P3", "K-a2")]] == [
        o.file_sha256 for o in cell.options
    ]


def test_verified_bank_packages_with_its_manifest_identity(built_bank, tmp_path):
    bank = qualified_dyad_bank(built_bank.bank_dir, expected_bank_sha256=built_bank.bank_sha256)
    result = build_dyad_package(bank, tmp_path / "pkg")
    assert result.manifest["bank"] == bank.package_bank()
    assert result.manifest["package_id"] == built_bank.bank_id
    loaded = load_package(result.path)
    assert loaded.combinations_checked == 1536
    audio = json.loads((result.path / "audio.json").read_text(encoding="utf-8"))
    manifest = read_manifest(built_bank.bank_dir)
    expected = {
        (cell.profile, cell.atom_id, o.rank): (o.pcm_sha256, o.file_sha256)
        for cell in manifest.cells
        for o in cell.options
    }
    found = {
        (o["profile"], o["atom_id"], o["rank"]): (o["pcm_sha256"], o["file_sha256"])
        for o in audio["options"]
    }
    assert found == expected and len(found) == 192


def test_wrong_pin_is_refused(built_bank):
    with pytest.raises(HandoffError, match="not the pinned bank hash"):
        qualified_dyad_bank(built_bank.bank_dir, expected_bank_sha256="0" * 64)
    with pytest.raises(HandoffError, match="lowercase"):
        qualified_dyad_bank(built_bank.bank_dir, expected_bank_sha256="A" * 64)


def test_changed_wav_or_manifest_is_refused(built_bank, bank_copy):
    manifest = read_manifest(bank_copy.root)
    wav = bank_copy.option(manifest.cells[11].options[0].wav)
    data = bytearray(wav.read_bytes())
    data[-1] ^= 0x01
    wav.write_bytes(bytes(data))
    with pytest.raises(HandoffError, match="does not verify") as err:
        qualified_dyad_bank(bank_copy.root, expected_bank_sha256=built_bank.bank_sha256)
    assert err.value.code == "E_HANDOFF" and err.value.problems


def test_edited_manifest_is_refused(built_bank, bank_copy):
    doc = json.loads(bank_copy.manifest.read_text(encoding="utf-8"))
    first = doc["cells"][3]["options"]
    first[0]["pcm_sha256"], first[1]["pcm_sha256"] = first[1]["pcm_sha256"], first[0]["pcm_sha256"]
    bank_copy.manifest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", "utf-8")
    with pytest.raises(HandoffError, match="does not verify"):
        qualified_dyad_bank(bank_copy.root, expected_bank_sha256=built_bank.bank_sha256)


def test_amended_bank_is_refused_until_menus_apply_amendments(built_bank, bank_copy):
    manifest = read_manifest(bank_copy.root)
    amend_bank(
        bank_copy.root,
        profile="P1",
        atom_id=manifest.atom_order[0],
        rank=2,
        reason="DEMO handoff test",
        unheard_confirmed=True,
    )
    with pytest.raises(HandoffError, match="amendments"):
        qualified_dyad_bank(bank_copy.root, expected_bank_sha256=built_bank.bank_sha256)

"""The generation config document, its frozen hash and the start-of-run checks."""

import dataclasses
from pathlib import Path

import pytest
from av_sound import load_fallback

from av_generation import genconfig as gc
from av_generation.constants import FROZEN_DECODING
from av_generation.ids import RunKind
from av_generation.meanings import load_meanings

ROOT = Path(__file__).resolve().parents[2]
H = "e" * 64


def _config(name: str = "DEMO-gen-01", llm: str | None = None) -> gc.GenerationConfig:
    fallback = load_fallback(ROOT / "sound/testvectors/fallback/demo-manifest.json")
    meanings = load_meanings(ROOT / "generation/examples/demo-meanings")
    return gc.build_generation_config(
        name,
        llm_manifest_sha256=llm,
        decoding_schema_sha256=H,
        prompts=gc.PromptHashes(H, H),
        meanings_sha256=meanings.sha256(),
        separation_threshold="0.10",
        fallback=gc.fallback_pins(fallback),
    )


def _freeze(value: str, status: str = "frozen") -> dict:
    return {"status": status, "items": [{"key": gc.FREEZE_CONFIG_KEY, "value": value}]}


def test_config_document_and_hash(tmp_path):
    config = _config()
    assert config.schema_errors() == ()
    assert config.decoding.temperature == FROZEN_DECODING.temperature
    assert config.decoding.max_input_tokens == 16_384
    assert set(config.fallback.books_sha256) == {"P1", "P2", "P3"}
    assert config.frozen_sha256() == _config().frozen_sha256()
    assert (
        config.frozen_sha256() != dataclasses.replace(config, separation_threshold="0.11").sha256()
    )
    path = tmp_path / gc.GENERATION_CONFIG_NAME
    config.write(path)
    assert gc.GenerationConfig.read(path).frozen_sha256() == config.frozen_sha256()
    assert gc.config_differences(config) == ()


def test_real_configs_need_the_llm_manifest():
    assert _config("frozen-1-0", llm=H).schema_errors() == ()
    with pytest.raises(Exception, match="llm_manifest_sha256"):
        _config("frozen-1-0", llm=None)


def test_check_run_config_rules():
    demo = _config()
    gc.check_run_config(demo, kind=RunKind.SYNTHETIC)
    real = _config("frozen-1-0", llm=H)
    gc.check_run_config(real, kind=RunKind.PILOT)
    gc.check_run_config(real, kind="confirmatory", freeze_manifest=_freeze(real.frozen_sha256()))
    cases = [
        (demo, RunKind.CONFIRMATORY, None, gc.E_CONFIG_KIND),
        (real, RunKind.SYNTHETIC, None, gc.E_CONFIG_KIND),
        (real, RunKind.CONFIRMATORY, None, gc.E_FREEZE_MISSING),
        (real, RunKind.CONFIRMATORY, _freeze(real.frozen_sha256(), "draft"), gc.E_FREEZE_STATUS),
        (real, RunKind.CONFIRMATORY, _freeze(H), gc.E_FREEZE_MISMATCH),
        (real, RunKind.PILOT, {"status": "draft", "items": []}, gc.E_FREEZE_MISMATCH),
        (
            dataclasses.replace(real, code=dataclasses.replace(real.code, renderer_hash=H)),
            RunKind.PILOT,
            None,
            gc.E_CONFIG_CODE,
        ),
        (
            dataclasses.replace(real, budget_b=dataclasses.replace(real.budget_b, max_attempts=5)),
            RunKind.PILOT,
            None,
            gc.E_CONFIG_CODE,
        ),
    ]
    for config, kind, manifest, code in cases:
        with pytest.raises(gc.ConfigMismatch) as err:
            gc.check_run_config(config, kind=kind, freeze_manifest=manifest)
        assert err.value.code == code
    with pytest.raises(KeyError):
        gc.freeze_item({"items": []}, gc.FREEZE_CONFIG_KEY)

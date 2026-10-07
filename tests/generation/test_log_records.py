"""Record and document contracts: schema validity, JSON round trip, canonical lines."""

import dataclasses
import json
from functools import cache
from pathlib import Path

import pytest
from av_sound import Profile, load_fallback, render, scan_fallback, wav_bytes
from av_sound.store import validator_code_hash
from av_sound.synthetic import synthetic_recipes
from av_sound.version import renderer_hash
from av_sound.wav import file_sha256

from av_generation import records as rec
from av_generation._schemas import load_schema
from av_generation.ids import Method, RunKind, Study, proposal_slot_id
from av_generation.jsonio import CodecError, canonical_line, from_json_value, to_json_value
from av_generation.outcomes import LlmStatus, SlotOutcome
from av_generation.records import (
    DOCUMENT_TYPES,
    RECORD_TYPES,
    RecordError,
    RecordWriter,
    read_records,
    record_from_dict,
)
from av_generation.rundir import LOG_FILES

ROOT = Path(__file__).resolve().parents[2]
BOOK = "DEMO-BK-H9TC"
H = "a" * 64


@cache
def _base():
    recipe = synthetic_recipes(Profile.P1)["K-a1"]
    rendered = render(recipe, Profile.P1)
    return recipe, rendered.pcm_sha256, file_sha256(wav_bytes(rendered))


@cache
def _scan():
    fset = load_fallback(ROOT / "sound/testvectors/fallback/demo-manifest.json")
    return scan_fallback(fset.bank("P1"), []).to_dict()


def samples() -> dict[str, rec._Tagged]:
    recipe, pcm, wav = _base()
    sid = proposal_slot_id(BOOK, "K-a1", 2, 3)
    parent = proposal_slot_id(BOOK, "K-a1", 1, 2)
    return {
        "slot_a3": rec.SlotRecord(
            run_id="DEMO-run-01",
            study=Study.A,
            method=Method.A3,
            slot_id=sid,
            profile=Profile.P1,
            atom_id="K-a1",
            slot=3,
            slot_index=6,
            outcome=SlotOutcome.VALID,
            t_open_ms=1000,
            t_ms=2500,
            batch_id="DEMO-A-P01",
            book_id=BOOK,
            round=2,
            seed_key="A3|DEMO-A-P01|K-a1|2|3",
            seed=2**64 - 1,
            prompt_sha256=H,
            schema_sha256=H,
            llm_status=LlmStatus.OK,
            tokens_in=900,
            tokens_out=60,
            latency_ms=1500,
            raw_output=recipe.canonical_json(),
            recipe=recipe.to_dict(),
            recipe_sha256=recipe.sha256(),
            pcm_sha256=pcm,
            file_sha256=wav,
        ),
        "slot_a2": rec.SlotRecord(
            run_id="DEMO-run-01",
            study=Study.A,
            method=Method.A2,
            slot_id=proposal_slot_id(BOOK, "K-a1", 2, 1),
            profile=Profile.P1,
            atom_id="K-a1",
            slot=1,
            slot_index=4,
            outcome=SlotOutcome.SEPARATION_FAIL,
            t_open_ms=0,
            t_ms=3,
            batch_id="DEMO-A-P01",
            book_id=BOOK,
            round=2,
            seed_key="A2|DEMO-A-P01|K-a1|2|1",
            seed=12,
            latency_ms=3,
            recipe=recipe.to_dict(),
            recipe_sha256=recipe.sha256(),
            validator_codes=("E_SEPARATION",),
            validator_messages=("too close",),
            pcm_sha256=pcm,
            a2=rec.A2Detail(
                mode="mutation",
                parent_slot_id=parent,
                mutations=(
                    rec.A2Mutation("pitch_1", 5, 2, 5, 4, True),
                    rec.A2Mutation("amplitude_2", 0.8, 1, 1.0, 1.0, False),
                ),
            ),
        ),
        "slot_b": rec.SlotRecord(
            run_id="DEMO-bank-run",
            study=Study.B,
            method=Method.B,
            slot_id="DEMO-bank-01.t1.P2.Q-r4.s07",
            profile=Profile.P2,
            atom_id="Q-r4",
            slot=7,
            slot_index=7,
            outcome=SlotOutcome.TIMEOUT,
            t_open_ms=0,
            t_ms=40_000,
            bank_id="DEMO-bank-01",
            attempt=1,
            seed_key="B|DEMO-bank-01|1|P2|Q-r4|7",
            seed=5,
            llm_status=LlmStatus.TIMEOUT,
            latency_ms=40_000,
        ),
        "refusal": rec.SlotRefusal(
            run_id="DEMO-run-01",
            study=Study.A,
            method=Method.A1,
            cap_key=f"A|{BOOK}|K-a1",
            reason="slot_cap",
            requested="slot-13",
            used=12,
            t_ms=9,
        ),
        "llm": rec.LlmRequest(
            run_id="DEMO-run-01",
            seed_key="A3|DEMO-A-P01|K-a1|2|3",
            seed=2**64 - 1,
            wire_seed=-1,
            model="Qwen/Qwen2.5-7B-Instruct",
            runtime="mock scripted",
            temperature=0.7,
            top_p=0.9,
            top_k=50,
            repetition_penalty=1.0,
            max_tokens=512,
            prompt_sha256=H,
            schema_sha256=H,
            status=LlmStatus.OK,
            latency_ms=1500,
            t_ms=2500,
            model_revision="a09a35458c702b33eeacc393d103063234e8bc28",
            finish_reason="stop",
            tokens_in=900,
            tokens_out=60,
            slot_id=sid,
        ),
        "rating": rec.RatingRecord(
            run_id="DEMO-run-01",
            batch_id="DEMO-A-P01",
            book_id=BOOK,
            atom_id="K-a1",
            round=2,
            position=5,
            rating_slot_id="DEMO-A-P01.K-a1.r2p5",
            slot_id=sid,
            rater_id="R2",
            station="S2",
            rater_kind="bot",
            placeholder=False,
            first_atom=True,
            association=6,
            distinguishability=4,
            distinguishability_by_rule=True,
            comfort="acceptable",
            missing=False,
            reconnected=False,
            slot_start_ms=10_000,
            t_ms=25_000,
            candidate_onset_ms=12,
            reference_onset_ms=None,
            unlock_ms=2000,
            rt_ms=4100,
        ),
        "rating_placeholder": rec.RatingRecord(
            run_id="DEMO-run-01",
            batch_id="DEMO-A-P01",
            book_id=BOOK,
            atom_id="K-a1",
            round=2,
            position=6,
            rating_slot_id="DEMO-A-P01.K-a1.r2p6",
            slot_id=sid,
            rater_id="R2",
            station="S2",
            rater_kind="human",
            placeholder=True,
            first_atom=False,
            association=None,
            distinguishability=None,
            distinguishability_by_rule=False,
            comfort=None,
            missing=False,
            reconnected=False,
            slot_start_ms=30_000,
            t_ms=50_000,
        ),
        "decision": rec.DecisionRecord(
            run_id="DEMO-run-01",
            batch_id="DEMO-A-P01",
            book_id=BOOK,
            atom_id="K-a1",
            round=4,
            first_atom=False,
            candidates=(rec.CandidateScore(sid, 6, True, 3, 2, True, "11/2", 3, False),),
            incumbent_slot_id=sid,
            incumbent_score="11/2",
            incumbent_changed=True,
            final=True,
            action="commit",
            book_substituted=False,
            t_ms=99,
        ),
        "decision_archive": rec.DecisionRecord(
            run_id="DEMO-run-01",
            batch_id="DEMO-A-P01",
            book_id=BOOK,
            atom_id="K-a2",
            round=4,
            first_atom=False,
            candidates=(),
            incumbent_slot_id=None,
            incumbent_score=None,
            incumbent_changed=False,
            final=True,
            action="archive_none",
            book_substituted=True,
            t_ms=120,
        ),
        "commit": rec.CommitRecord(
            run_id="DEMO-run-01",
            batch_id="DEMO-A-P01",
            book_id=BOOK,
            store_book_id=BOOK,
            atom_id="K-a1",
            semantic_label="ALIGN_ARROW",
            source="selector",
            store_source=sid,
            recipe=recipe.to_dict(),
            recipe_sha256=recipe.sha256(),
            pcm_sha256=pcm,
            file_sha256=wav,
            chain_head=H,
            failed_generation=False,
            t_ms=100,
            slot_id=sid,
        ),
        "scan": rec.FallbackScanRecord(
            run_id="DEMO-run-01",
            batch_id="DEMO-A-P01",
            book_id=BOOK,
            atom_id="K-a1",
            scan=_scan(),
            t_ms=101,
        ),
        "play": rec.PlayEvent(
            run_id="DEMO-run-01",
            context="rating_candidate",
            audio_kind="atom",
            asset_id=wav,
            result="played",
            t_ms=10_012,
            pcm_sha256=pcm,
            station="S1",
            actor_id="R1",
            rating_slot_id="DEMO-A-P01.K-a1.r2p5",
            scheduled_ms=10_000,
            onset_ms=10_012,
        ),
        "play_refused": rec.PlayEvent(
            run_id="DEMO-run-01",
            context="a1_preview",
            audio_kind="atom",
            asset_id=wav,
            result="refused",
            t_ms=5,
            reason="E_TOKEN_USED",
            slot_id=sid,
            token_id="tok_0123456789",
        ),
        "timing": rec.TimingEvent(
            run_id="DEMO-run-01",
            event="startup_end",
            t_ms=0,
            wall_utc="2026-11-05T09:30:00.000Z",
            component="llm",
            duration_ms=95_000,
        ),
        "session": rec.ThresholdSession(
            session_id="DEMO-S-01",
            set_id="DEMO-T1",
            set_sha256=H,
            listener_id="L01",
            station="S1",
            gain_db=-12.0,
            order_seed_key="THRESHOLD|DEMO-T1|order|DEMO-S-01",
            order_seed=7,
            ab_order_rule="balanced_per_bin",
            plan=(rec.ThresholdPlannedTrial(1, "P1-0.100-01", "BA"),),
            tryout=True,
            demo=True,
            created_utc="2026-11-05T09:30:00.000Z",
        ),
        "trial": rec.ThresholdTrial(
            run_id="DEMO-T-run",
            session_id="DEMO-S-01",
            listener_id="L01",
            trial_index=1,
            pair_id="P1-0.100-01",
            profile="P1",
            kind="different",
            bin_center="0.100",
            distance=0.1,
            order="BA",
            gap_ms=500,
            tryout=True,
            t_ms=7,
            response="same",
            rt_ms=900,
            onset_first_ms=1,
            onset_second_ms=1400,
        ),
        "manifest": rec.RunManifest(
            run_id="DEMO-run-01",
            kind=RunKind.DEMO,
            study=Study.A,
            purpose="dry_run",
            clock="scaled",
            created_utc="2026-11-05T09:30:00.000Z",
            code=rec.RunCode("0.1.0", "0.1.0", renderer_hash(), "0.1.0", validator_code_hash()),
            threshold="0.10",
            freeze_manifest_sha256=H,
            clock_speed=100.0,
            config_sha256=H,
            seed_namespace="DEMO-A-P01",
            generation_config_sha256=H,
            meanings_sha256=H,
            books=(rec.RunBook(BOOK, Method.A3), rec.RunBook("DEMO-BK-7QX4", Method.A1, "D1")),
            files={"logs/slots.jsonl": H},
        ),
        "stimuli": rec.ThresholdStimulusSet(
            set_id="DEMO-T1",
            demo=True,
            renderer_version="0.1.0",
            validator_version="0.1.0",
            config=rec.ThresholdConfig(("P1",), ("0.100",), "0.0125", 1, 0, 500, "0.10"),
            pairs=(
                rec.ThresholdPair(
                    pair_id="P1-0.100-01",
                    profile="P1",
                    kind="same",
                    bin_center=None,
                    recipe_a=recipe.to_dict(),
                    recipe_b=recipe.to_dict(),
                    pcm_sha256_a=pcm,
                    pcm_sha256_b=pcm,
                    file_sha256_a=wav,
                    file_sha256_b=wav,
                    sum_sq="0",
                    distance=0.0,
                    differing=(),
                    seed_key="THRESHOLD|DEMO-T1|pair|P1|0|1",
                    search_steps=0,
                ),
            ),
        ),
    }


@pytest.mark.parametrize("name", sorted(samples()))
def test_sample_matches_schema_and_round_trips(name):
    obj = samples()[name]
    assert obj.schema_errors() == ()
    data = obj.to_dict()
    again = type(obj).from_dict(json.loads(canonical_line(data)))
    assert again == obj
    assert canonical_line(again.to_dict()) == canonical_line(data)


def test_every_record_and_document_type_has_a_sample():
    kinds = {type(s) for s in samples().values()}
    assert set(RECORD_TYPES.values()) | set(DOCUMENT_TYPES.values()) <= kinds
    assert set(RECORD_TYPES) == set(LOG_FILES)


def _field_names(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


@pytest.mark.parametrize(
    "cls", [*RECORD_TYPES.values(), *DOCUMENT_TYPES.values()], ids=lambda c: c.__name__
)
def test_schema_properties_equal_dataclass_fields(cls):
    schema = load_schema(cls.SCHEMA)
    props = set(schema["properties"])
    assert props == _field_names(cls) | {cls.TAG_KEY, cls.VERSION_KEY}
    assert set(schema["required"]) == props
    assert schema["properties"][cls.TAG_KEY] == {"const": cls.TAG}
    assert schema["properties"][cls.VERSION_KEY] == {"const": cls.VERSION}


def test_nested_schemas_match_nested_dataclasses():
    slot = load_schema("slot-record.schema.json")["properties"]
    a2 = slot["a2"]["oneOf"][0]
    assert set(a2["properties"]) == _field_names(rec.A2Detail)
    assert set(a2["properties"]["mutations"]["items"]["properties"]) == _field_names(rec.A2Mutation)
    decision = load_schema("decision-record.schema.json")["properties"]
    assert set(decision["candidates"]["items"]["properties"]) == _field_names(rec.CandidateScore)
    stim = load_schema("threshold-stimuli.schema.json")["properties"]
    assert set(stim["config"]["properties"]) == _field_names(rec.ThresholdConfig)
    assert set(stim["pairs"]["items"]["properties"]) == _field_names(rec.ThresholdPair)
    manifest = load_schema("run-manifest.schema.json")["properties"]
    assert set(manifest["code"]["properties"]) == _field_names(rec.RunCode)
    assert set(manifest["books"]["items"]["properties"]) == _field_names(rec.RunBook)
    session = load_schema("threshold-session.schema.json")["properties"]
    assert set(session["plan"]["items"]["properties"]) == _field_names(rec.ThresholdPlannedTrial)


def test_enumerations_match_python_constants():
    play = load_schema("play-event.schema.json")["properties"]
    assert tuple(play["context"]["enum"]) == rec.PLAY_CONTEXTS
    timing = load_schema("timing-event.schema.json")["properties"]
    assert tuple(timing["event"]["enum"]) == rec.TIMING_EVENTS
    decision = load_schema("decision-record.schema.json")["properties"]
    assert tuple(decision["action"]["enum"]) == rec.DECISION_ACTIONS
    common = load_schema("common.schema.json")["$defs"]
    assert tuple(common["outcome"]["enum"]) == tuple(o.value for o in SlotOutcome)
    assert tuple(common["llm_status"]["enum"]) == tuple(s.value for s in LlmStatus)


@pytest.mark.parametrize(
    ("name", "change"),
    [
        ("slot_a3", {"extra": 1}),
        ("slot_a3", {"method": "B"}),
        ("slot_a3", {"book_id": None}),
        ("slot_a3", {"outcome": "nope"}),
        ("slot_a3", {"validator_codes": ["E_CLIP"]}),
        ("slot_a2", {"a2": None}),
        ("slot_b", {"round": 1}),
        ("slot_b", {"seed": 2**64}),
        ("llm", {"temperature": 0.8}),
        ("rating", {"association": 8}),
        ("rating", {"distinguishability": 5}),
        ("rating_placeholder", {"association": 3}),
        ("commit", {"source": "fallback_book"}),
        ("decision", {"action": "archive"}),
        ("decision", {"final": False}),
        ("decision_archive", {"book_substituted": False}),
        ("decision_archive", {"action": "fallback_scan"}),
        ("session", {"ab_order_rule": "random"}),
        ("play", {"audio_kind": "song"}),
        ("play_refused", {"reason": None}),
        ("timing", {"event": "lunch"}),
        ("manifest", {"run_id": "run-01"}),
    ],
)
def test_schema_rejects_bad_values(name, change):
    data = samples()[name].to_dict()
    data.update(change)
    with pytest.raises(RecordError):
        type(samples()[name]).from_dict(data)


def test_canonical_line_format():
    line = canonical_line({"b": 1, "a": "é", "c": [1.0, 0.6]})
    assert line == b'{"a":"\\u00e9","b":1,"c":[1.0,0.6]}\n'
    with pytest.raises(ValueError):
        canonical_line({"x": float("nan")})


def test_writer_and_reader(tmp_path):
    path = tmp_path / LOG_FILES["slot"]
    writer = RecordWriter(path, types=[rec.SlotRecord], fsync=False)
    for name in ("slot_a3", "slot_a2", "slot_b"):
        writer.append(samples()[name])
    assert writer.count == 3
    got = read_records(path, rec.SlotRecord)
    assert [r.slot_id for r in got] == [
        samples()[n].slot_id for n in ("slot_a3", "slot_a2", "slot_b")
    ]
    raw = path.read_bytes()
    assert raw.count(b"\n") == 3 and b"\r" not in raw
    with pytest.raises(RecordError):
        writer.append(samples()["play"])
    with pytest.raises(RecordError):
        RecordWriter(tmp_path / "x.jsonl").append(
            dataclasses.replace(samples()["slot_a3"], slot_index=13)
        )
    with pytest.raises(RecordError):
        read_records(path, rec.PlayEvent)


def test_torn_line_is_detected(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(canonical_line(samples()["timing"].to_dict())[:-5])
    with pytest.raises(CodecError):
        read_records(path, rec.TimingEvent)


def test_dispatch_and_document_io(tmp_path):
    assert isinstance(record_from_dict(samples()["rating"].to_dict()), rec.RatingRecord)
    with pytest.raises(RecordError):
        record_from_dict({"record": "unknown"})
    path = tmp_path / "run-manifest.json"
    digest = samples()["manifest"].write(path)
    assert len(digest) == 64
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n") and '  "format": "av-generation/run-manifest"' in text
    assert rec.RunManifest.read(path) == samples()["manifest"]
    with pytest.raises(FileExistsError):
        samples()["manifest"].write(path)


def test_cap_keys():
    assert samples()["slot_a3"].cap_key == f"A|{BOOK}|K-a1"
    assert samples()["slot_b"].cap_key == "B|DEMO-bank-01|1|P2|Q-r4"
    with pytest.raises(RecordError):
        rec.cap_key("A", "K-a1")
    with pytest.raises(RecordError):
        rec.cap_key("B", "K-a1", bank_id="x")


def test_codec_type_errors():
    assert to_json_value((SlotOutcome.VALID, 1.5)) == ["valid", 1.5]
    with pytest.raises(CodecError):
        to_json_value(object())
    with pytest.raises(CodecError):
        from_json_value(int, True)
    with pytest.raises(CodecError):
        from_json_value(tuple[int, ...], {"a": 1})
    with pytest.raises(CodecError):
        from_json_value(rec.A2Mutation, {"coordinate": "pitch_1"})
    assert from_json_value(int | float, 0.6) == 0.6
    assert from_json_value(int | float, 5) == 5


def test_confirmatory_manifests_need_the_config_and_freeze_hashes():
    base = dataclasses.replace(
        samples()["manifest"], run_id="run-01", kind=RunKind.CONFIRMATORY, purpose="batch"
    )
    assert base.schema_errors() == ()
    for field in ("generation_config_sha256", "meanings_sha256"):
        assert dataclasses.replace(base, **{field: None}).schema_errors()
    assert dataclasses.replace(base, freeze_manifest_sha256=None).schema_errors() != ()
    pilot = dataclasses.replace(base, kind=RunKind.PILOT, freeze_manifest_sha256=None)
    assert pilot.schema_errors() == ()
    assert dataclasses.replace(pilot, generation_config_sha256=None).schema_errors()
    practice = dataclasses.replace(
        base, kind=RunKind.PRACTICE, purpose="practice", generation_config_sha256=None
    )
    assert dataclasses.replace(practice, freeze_manifest_sha256=None).schema_errors() == ()

"""Tests of the macOS demo bridge (`sound/demo/macos/bridge/av_sound_bridge.py`).

The bridge implements `sound/demo/macos/PROTOCOL.md`. These tests run the dispatcher
in-process for every command, its error paths and the envelope rules, then one
end-to-end round trip through a subprocess. Synthetic recipes and DEMO books only; the
bridge keeps its stores and packages in a temporary directory outside every git work
tree.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import signal
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from av_sound import Profile, Recipe, VocabularyStore, distance, render
from av_sound._schemas import schema_validator
from av_sound.fallback import inside_work_tree
from av_sound.grammar import HELDOUT_MESSAGE_IDS
from av_sound.synthetic import synthetic_recipes

REPO = Path(__file__).resolve().parents[2]
SOUND_ROOT = REPO / "sound"
DEMO_DIR = SOUND_ROOT / "demo" / "macos"
BRIDGE_PATH = DEMO_DIR / "bridge" / "av_sound_bridge.py"
PROTOCOL = DEMO_DIR / "PROTOCOL.md"
COMPOSITION_VECTORS = SOUND_ROOT / "testvectors" / "composition" / "vectors.json"
RENDERER_VECTORS = SOUND_ROOT / "testvectors" / "renderer" / "vectors.json"
GOLDEN_MANIFEST = REPO / "tests" / "golden" / "manifest.json"
EXAMPLE_MANIFEST = SOUND_ROOT / "examples" / "package-demo" / "manifest.json"
FALLBACK_MANIFEST = SOUND_ROOT / "testvectors" / "fallback" / "demo-manifest.json"


if sys.platform == "win32":  # pragma: no cover - the sound workflow also runs on Windows
    # The bridge belongs to the macOS demo: these tests drive it as a POSIX process (SIGTERM,
    # SIGHUP, TMPDIR). The engine's own tests in this directory still run on Windows.
    pytest.skip("the macOS demo bridge is tested on POSIX systems only", allow_module_level=True)


def _load_bridge(name: str = "av_sound_bridge") -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, BRIDGE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bridge_mod = _load_bridge()

WORKED = {
    "total_ms": 600,
    "pitches": [-3, 0, 4],
    "rhythm_weights": [2, 1, 3],
    "gaps_ms": [40, 20],
    "amplitudes": [1.0, 0.6, 0.8],
}
WORKED_P2_PCM = "4c0467de354c076c0b30bc9af794e31384afdf161af24605fb621cc455c35d87"
"""The renderer self-test pin of the worked example (P2)."""
SHORT_EVENT = {
    "total_ms": 450,
    "pitches": [0, 0, 0],
    "rhythm_weights": [1, 4, 4],
    "gaps_ms": [60, 60],
    "amplitudes": [1.0, 0.8, 0.6],
}
"""First event 1,760 samples: rejected with E_EVENT_SHORT."""
P1 = {a: r.to_dict() for a, r in synthetic_recipes(Profile.P1).items()}
"""The synthetic DEMO-P1 recipes by atom ID."""


def _ref(atom_id: str, recipe: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "ref_id": atom_id,
        "recipe": P1[atom_id] if recipe is None else recipe,
        **extra,
    }


@pytest.fixture(scope="module")
def bridge(tmp_path_factory: pytest.TempPathFactory) -> Any:
    b = bridge_mod.Bridge(temp_base=tmp_path_factory.mktemp("bridge"))
    yield b
    b.close()


@pytest.fixture()
def fresh(tmp_path: Path) -> Any:
    b = bridge_mod.Bridge(temp_base=tmp_path / "base")
    yield b
    b.close()


def call(b: Any, cmd: str, args: Any = None, rid: int = 7) -> dict[str, Any]:
    """Dispatch one request and pass the response through the wire encoding."""
    request: dict[str, Any] = {"id": rid, "cmd": cmd}
    if args is not None:
        request["args"] = args
    response = b.dispatch(request)
    line = bridge_mod.encode(response)
    assert line.endswith(b"\n") and b"\n" not in line[:-1]
    decoded: dict[str, Any] = json.loads(line)
    assert decoded["id"] == rid
    return decoded


def ok(b: Any, cmd: str, args: Any = None) -> Any:
    response = call(b, cmd, args)
    assert response["ok"] is True, response
    return response["result"]


def err(b: Any, cmd: str, args: Any = None) -> dict[str, Any]:
    response = call(b, cmd, args)
    assert response["ok"] is False, response
    assert set(response["error"]) >= {"type", "code", "message"}
    error: dict[str, Any] = response["error"]
    return error


def check_audio(result: dict[str, Any]) -> bytes:
    """The client rule: sha256(base64decode(wav_b64)) == file_sha256."""
    data = base64.b64decode(result["wav_b64"])
    assert hashlib.sha256(data).hexdigest() == result["file_sha256"]
    assert hashlib.sha256(data[44:]).hexdigest() == result["pcm_sha256"]
    assert data[:4] == b"RIFF" and data[8:16] == b"WAVEfmt "
    return data


# ---------------------------------------------------------------------------
# Contract coverage and envelope


def test_every_protocol_command_is_implemented(bridge: Any) -> None:
    text = PROTOCOL.read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", text, flags=re.MULTILINE)) - {"cmd"}
    assert len(documented) == 28
    assert documented == set(bridge.commands)


@pytest.mark.parametrize(
    "line",
    [
        b"not json",
        b"\xff\xfe{}",
        b"",
        b"   ",
        b'{"id": 1, "cmd": "hello", "x": NaN}',
        b"{",
    ],
)
def test_a_line_that_is_not_json_gives_id_null(bridge: Any, line: bytes) -> None:
    response = bridge.handle_line(line)
    assert response["id"] is None and response["ok"] is False
    assert response["error"]["type"] == "ProtocolError"
    assert response["error"]["code"] == "E_BAD_REQUEST"
    json.loads(bridge_mod.encode(response))


@pytest.mark.parametrize(
    ("request_obj", "echo"),
    [
        ([1, 2], None),
        ({"cmd": "hello"}, None),
        ({"id": 0, "cmd": "hello"}, None),
        ({"id": -3, "cmd": "hello"}, None),
        ({"id": True, "cmd": "hello"}, None),
        ({"id": "5", "cmd": "hello"}, None),
        ({"id": 5}, 5),
        ({"id": 5, "cmd": 3}, 5),
        ({"id": 5, "cmd": "hello", "args": [1]}, 5),
    ],
)
def test_bad_envelopes(bridge: Any, request_obj: Any, echo: int | None) -> None:
    response = bridge.handle_line(json.dumps(request_obj))
    assert response == {
        "id": echo,
        "ok": False,
        "error": {
            "type": "ProtocolError",
            "code": "E_BAD_REQUEST",
            "message": response["error"]["message"],
        },
    }


def test_unknown_command_echoes_the_id(bridge: Any) -> None:
    response = bridge.handle_line(b'{"id": 42, "cmd": "rm_rf", "args": {}}\n')
    assert response["id"] == 42 and response["ok"] is False
    assert response["error"]["type"] == "ProtocolError"
    assert response["error"]["code"] == "E_UNKNOWN_CMD"


def test_args_may_be_omitted_or_null(bridge: Any) -> None:
    assert call(bridge, "grammar")["ok"] is True
    assert bridge.handle_line(b'{"id": 3, "cmd": "grammar", "args": null}')["ok"] is True


def test_missing_and_mistyped_arguments_are_protocol_errors(bridge: Any) -> None:
    for cmd, args in [
        ("render", {"profile": "P1"}),
        ("render", {"recipe": WORKED}),
        ("render", {"recipe": WORKED, "profile": 1}),
        ("render", {"recipe": [600], "profile": "P1"}),
        ("render", {"recipe": json.dumps(WORKED), "profile": "P1"}),
        ("features", {"recipe": None}),
        ("random_recipe", {}),
        ("random_recipe", {"seed": "1"}),
        ("random_recipe", {"seed": True}),
        ("random_recipe", {"seed": 1, "admissible_only": "yes"}),
        ("distance", {"a": WORKED}),
        ("distance", {"a": WORKED, "b": "x"}),
        ("distance", {"a": 5, "b": WORKED}),
        ("validate", {"profile": "P1"}),
        ("validate", {"candidate": WORKED, "profile": "P1", "committed": {}}),
        (
            "validate",
            {"candidate": WORKED, "profile": "P1", "committed": [{"recipe": WORKED}]},
        ),
        (
            "validate",
            {"candidate": WORKED, "profile": "P1", "committed": [{"ref_id": "x"}]},
        ),
        ("validate", {"candidate": WORKED, "profile": "P1", "committed": ["K-a1"]}),
        ("validate", {"candidate": WORKED, "profile": "P1", "use_reserved": 1}),
        (
            "validate",
            {"candidate": WORKED, "profile": "P1", "committed": [_ref("K-a1", "x")]},
        ),
        ("validate", {"candidate": WORKED, "profile": "P1", "threshold": 0.1}),
        ("validate", {"candidate": WORKED, "profile": "P1", "threshold": 1}),
        ("validate", {"candidate": WORKED, "profile": "P1", "threshold": True}),
        ("validate", {"candidate": WORKED, "profile": "P1", "threshold": [1]}),
        ("nearest", {"candidate": WORKED, "profile": "P1"}),
        ("nearest", {"candidate": "x", "profile": "P1", "committed": []}),
        ("compose", {"referent": _ref("K-r1"), "profile": "P1"}),
        ("compose", {"action": _ref("K-a1", 5), "referent": _ref("K-r1"), "profile": "P1"}),
        ("nonlexical_get", {}),
        ("store_create", {"profile": "P1"}),
        ("store_create", {"book_id": 7, "profile": "P1"}),
        ("store_create", {"book_id": "DEMO-X1", "profile": "P1", "threshold": 0.1}),
        ("store_commit", {"book_id": "DEMO-X1", "recipe": WORKED}),
        # semantic_label is required: a string or an explicit null, never absent.
        ("store_commit", {"book_id": "DEMO-X1", "atom_id": "K-a1", "recipe": WORKED}),
        (
            "store_commit",
            {"book_id": "DEMO-X1", "atom_id": "K-a1", "semantic_label": 5, "recipe": WORKED},
        ),
        (
            "store_commit",
            {"book_id": "DEMO-X1", "atom_id": "K-a1", "semantic_label": "ADD_ONE", "recipe": "{}"},
        ),
        ("fallback_scan", {"profile": "P1"}),
        ("fallback_scan", {"profile": "P1", "book": [], "used": 3}),
        ("fallback_scan", {"profile": "P1", "book": [], "used": ["a"]}),
        ("fallback_scan", {"profile": "P1", "book": [], "used": [True]}),
        ("fallback_scan", {"profile": "P1", "book": [], "used": [1.0]}),
        ("fallback_scan", {"profile": "P1", "book": [_ref("K-a1", [1])]}),
    ]:
        error = err(bridge, cmd, args)
        assert (error["type"], error["code"]) == ("ProtocolError", "E_BAD_REQUEST"), (
            cmd,
            args,
        )


def test_a_failing_handler_never_crashes_the_loop(fresh: Any) -> None:
    def boom(args: Any) -> Any:
        raise RuntimeError("internal")

    fresh.commands["hello"] = boom
    fresh.commands["grammar"] = lambda args: {"value": float("nan")}  # not encodable
    stdin = io.BytesIO(
        b'{"id":1,"cmd":"hello"}\n{"id":2,"cmd":"grammar"}\n{"id":3,"cmd":"self_test"}\n'
    )
    stdout = io.BytesIO()
    assert bridge_mod.serve(fresh, stdin, stdout) == 0  # end of input
    lines = [json.loads(x) for x in stdout.getvalue().splitlines()]
    assert [x["id"] for x in lines] == [1, 2, 3]
    assert lines[0]["error"] == {
        "type": "RuntimeError",
        "code": None,
        "message": "internal",
    }
    assert lines[1]["ok"] is False and lines[1]["error"]["type"] == "ValueError"
    assert lines[2]["ok"] is True


def test_error_fields_map_engine_codes() -> None:
    assert bridge_mod.error_fields(KeyError("unknown asset 'x'")) == {
        "type": "KeyError",
        "code": None,
        "message": "unknown asset 'x'",
    }
    from av_sound import RecipeError

    assert bridge_mod.error_fields(RecipeError("E_DOMAIN", "m"))["code"] == "E_DOMAIN"


# ---------------------------------------------------------------------------
# Engine facts and motifs


def test_hello(bridge: Any) -> None:
    result = ok(bridge, "hello")
    assert result["bridge_version"] == 1
    assert result["sample_rate"] == 48_000 and result["gap_samples"] == 9_600
    assert result["threshold"] == "0.1" and result["threshold_float"] == 0.1
    assert result["profiles"] == [
        {"id": "P1", "f0_hz": 300},
        {"id": "P2", "f0_hz": 450},
        {"id": "P3", "f0_hz": 675},
    ]
    assert result["domain"]["total_ms"] == [450, 600, 750, 900]
    assert result["domain"]["amplitudes"] == [0.6, 0.8, 1.0]
    assert len(result["feature_names"]) == 12
    assert result["reason_codes"][0] == "E_JSON" and "E_SEPARATION" in result["reason_codes"]
    vectors = json.loads(RENDERER_VECTORS.read_text("utf-8"))
    for key in ("renderer_version", "renderer_hash", "renderer_recipe_schema_hash"):
        assert result[key] == vectors[key]
    golden = json.loads(GOLDEN_MANIFEST.read_text("utf-8"))
    assert result["validator_hash"] == golden["validator_hash"]
    assert result["python"].startswith("3.11") and result["numpy"]


def test_self_test(bridge: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ok(bridge, "self_test")["ok"] is True

    def broken() -> None:
        raise RuntimeError("renderer self-test failed for P2")

    monkeypatch.setattr(bridge_mod, "self_test", broken)
    assert ok(bridge, "self_test") == {
        "ok": False,
        "message": "renderer self-test failed for P2",
    }


def test_render_worked_example(bridge: Any) -> None:
    result = ok(bridge, "render", {"recipe": WORKED, "profile": "P2"})
    assert result["pcm_sha256"] == WORKED_P2_PCM
    assert result["recipe"] == WORKED and result["profile"] == "P2"
    assert result["recipe_sha256"] == Recipe.from_dict(WORKED).sha256()
    assert result["n_samples"] == 28_800 and result["duration_ms"] == 600
    assert result["event_samples"] == [8640, 4320, 12960]
    assert result["event_onsets"] == [0, 10560, 15840]
    assert result["gap_samples"] == [1920, 960]
    assert result["short_event"] is False and result["overflow"] is False
    assert result["nonfinite"] is False and result["renderer_version"] == "0.1.0"
    assert result["peak"] > 0 and result["peak_dbfs"] < 0 and result["rms"] > 7000
    assert len(check_audio(result)) == 44 + 2 * 28_800


def test_render_is_fast(bridge: Any) -> None:
    args = {"recipe": {**WORKED, "total_ms": 900}, "profile": "P3"}
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        bridge_mod.encode(bridge.dispatch({"id": 1, "cmd": "render", "args": args}))
        timings.append(time.perf_counter() - start)
    assert min(timings) < 0.1


def test_render_flags_short_events_and_keeps_audio(bridge: Any) -> None:
    result = ok(bridge, "render", {"recipe": SHORT_EVENT, "profile": "P1"})
    assert result["short_event"] is True and result["event_samples"][0] == 1760
    check_audio(result)


def test_render_errors(bridge: Any) -> None:
    error = err(bridge, "render", {"recipe": {**WORKED, "total_ms": 601}, "profile": "P1"})
    assert (error["type"], error["code"]) == ("RecipeError", "E_DOMAIN")
    error = err(bridge, "render", {"recipe": {**WORKED, "extra": 1}, "profile": "P1"})
    assert (error["type"], error["code"]) == ("RecipeError", "E_SCHEMA")
    error = err(bridge, "render", {"recipe": {"total_ms": 600}, "profile": "P1"})
    assert (error["type"], error["code"]) == ("RecipeError", "E_SCHEMA")
    error = err(bridge, "render", {"recipe": {**WORKED, "total_ms": 600.0}, "profile": "P1"})
    assert error["code"] == "E_SCHEMA"
    error = err(bridge, "render", {"recipe": WORKED, "profile": "P9"})
    assert (error["type"], error["code"]) == ("ValueError", None)


def test_random_recipe_is_deterministic_and_admissible(bridge: Any) -> None:
    seen = set()
    for seed in range(200):
        recipe = ok(bridge, "random_recipe", {"seed": seed})["recipe"]
        assert (
            recipe == ok(bridge, "random_recipe", {"seed": seed, "admissible_only": True})["recipe"]
        )
        rendered = render(recipe, Profile.P1)
        assert min(rendered.event_samples) >= 2_880
        seen.add(json.dumps(recipe, sort_keys=True))
    assert len(seen) > 190
    free = [ok(bridge, "random_recipe", {"seed": s, "admissible_only": False}) for s in range(200)]
    short = [r for r in free if render(r["recipe"], Profile.P1).short_event]
    assert short, "without admissible_only some draws have short events"
    assert ok(bridge, "random_recipe", {"seed": -5})["recipe"]
    assert ok(bridge, "random_recipe", {"seed": 2**70})["recipe"]


def test_features(bridge: Any) -> None:
    result = ok(bridge, "features", {"recipe": WORKED})
    assert result["names"][0] == "pitch_1" and len(result["names"]) == 12
    assert len(result["exact"]) == len(result["values"]) == 12
    for exact, value in zip(result["exact"], result["values"], strict=True):
        assert float(Fraction(exact)) == value
    assert result["exact"][:4] == ["0.25", "0.5", "5/6", "1/3"]
    assert err(bridge, "features", {"recipe": {}})["code"] == "E_SCHEMA"


def test_fraction_text_in_results(bridge: Any) -> None:
    """PROTOCOL.md, "Fractions in results": where a float comes with the exact text, and
    which text form each result uses."""
    hello = ok(bridge, "hello")
    assert (hello["threshold"], hello["threshold_float"]) == ("0.1", 0.1)
    features = ok(bridge, "features", {"recipe": WORKED})
    validation = ok(bridge, "validate", {"candidate": WORKED, "profile": "P2"})
    assert features["exact"][:6] == ["0.25", "0.5", "5/6", "1/3", "0.4", "0.1"]
    assert validation["features"][:6] == ["1/4", "1/2", "5/6", "1/3", "2/5", "1/10"]
    assert validation["features"] == [str(Fraction(x)) for x in features["exact"]]
    assert validation["threshold"] == "0.1" and "threshold_float" not in validation
    scan = ok(bridge, "fallback_scan", {"profile": "P2", "book": []})
    assert (scan["threshold"], scan["bank_threshold"]) == ("0.1", "0.1")
    assert not [k for k in scan if k.endswith("_float")]
    near = ok(
        bridge,
        "nearest",
        {"candidate": WORKED, "profile": "P2", "committed": [_ref("K-a1")]},
    )
    assert isinstance(near["sum_sq"], str) and isinstance(near["distance"], float)
    assert near["distance"] == pytest.approx(float(Fraction(near["sum_sq"]) / 12) ** 0.5)
    pair = ok(bridge, "distance", {"a": WORKED, "b": {**WORKED, "pitches": [-3, 0, 3]}})
    assert pair["sum_sq"] == "1/144" and "sum_sq_float" not in pair


def test_distance(bridge: Any) -> None:
    same = ok(bridge, "distance", {"a": WORKED, "b": WORKED})
    assert same == {"distance": 0.0, "sum_sq": "0", "separated": False}
    other = P1["K-a1"]
    result = ok(bridge, "distance", {"a": WORKED, "b": other})
    expected = distance(Recipe.from_dict(WORKED), Recipe.from_dict(other))
    assert result["distance"] == expected and result["separated"] is (expected >= 0.1)
    assert Fraction(result["sum_sq"]) > 0
    assert err(bridge, "distance", {"a": WORKED, "b": {**other, "pitches": [9, 0, 0]}})["code"] == (
        "E_DOMAIN"
    )


# ---------------------------------------------------------------------------
# Validator


def test_validate_accepts_and_rejects_as_results(bridge: Any) -> None:
    result = ok(bridge, "validate", {"candidate": WORKED, "profile": "P2"})
    assert result["ok"] is True and result["codes"] == []
    assert result["pcm_sha256"] == WORKED_P2_PCM and result["threshold"] == "0.1"
    assert result["nearest_id"] is None and len(result["features"]) == 12
    schema_errors = list(schema_validator("validation-result.schema.json").iter_errors(result))
    assert not schema_errors

    text = json.dumps(WORKED)
    assert ok(bridge, "validate", {"candidate": text, "profile": "P2"})["ok"] is True
    bad_text = ok(bridge, "validate", {"candidate": "{not json", "profile": "P2"})
    assert bad_text["ok"] is False and bad_text["codes"] == ["E_JSON"]
    short = ok(bridge, "validate", {"candidate": SHORT_EVENT, "profile": "P1"})
    assert short["codes"] == ["E_EVENT_SHORT"]
    domain = ok(
        bridge,
        "validate",
        {"candidate": {**WORKED, "gaps_ms": [41, 20]}, "profile": "P1"},
    )
    assert domain["codes"] == ["E_DOMAIN"]
    assert ok(bridge, "validate", {"candidate": 5, "profile": "P1"})["codes"] == ["E_SCHEMA"]


def test_validate_against_committed_references(bridge: Any) -> None:
    committed = [_ref("K-a1"), _ref("K-a2", WORKED)]
    result = ok(
        bridge,
        "validate",
        {"candidate": WORKED, "profile": "P1", "committed": committed},
    )
    assert result["codes"] == ["E_DUPLICATE", "E_SEPARATION"]
    assert result["nearest_id"] == "K-a2" and result["nearest_index"] == 1
    assert result["nearest_distance"] == 0.0
    near = {**WORKED, "pitches": [-3, 0, 5]}  # distance 1/12 / sqrt(12), about 0.024
    close = ok(bridge, "validate", {"candidate": near, "profile": "P1", "committed": committed})
    assert close["codes"] == ["E_SEPARATION"]
    loose = ok(
        bridge,
        "validate",
        {
            "candidate": near,
            "profile": "P1",
            "committed": committed,
            "threshold": "0.01",
            "use_reserved": False,
        },
    )
    assert loose["ok"] is True and loose["threshold"] == "0.01"
    error = err(bridge, "validate", {"candidate": near, "profile": "P1", "threshold": 0.01})
    assert (error["type"], error["code"]) == ("ProtocolError", "E_BAD_REQUEST")
    # An input threshold is a plain non-negative decimal (PROTOCOL.md); the engine refuses
    # anything else, `p/q` included.
    for refused in ("-1", "1/10", ".1", "0.1e0"):
        error = err(bridge, "validate", {"candidate": near, "profile": "P1", "threshold": refused})
        assert (error["type"], error["code"]) == ("ValueError", None), refused
        error = err(
            bridge, "store_create", {"book_id": "DEMO-TH", "profile": "P1", "threshold": refused}
        )
        assert (error["type"], error["code"]) == ("ValueError", None), refused
    bad_ref = [{"ref_id": "K-a1", "recipe": {**WORKED, "total_ms": 1}}]
    error = err(bridge, "validate", {"candidate": near, "profile": "P1", "committed": bad_ref})
    assert (error["type"], error["code"]) == ("RecipeError", "E_DOMAIN")


def test_nearest(bridge: Any) -> None:
    args = {"candidate": WORKED, "profile": "P1", "committed": []}
    assert call(bridge, "nearest", args) == {"id": 7, "ok": True, "result": None}
    committed = [_ref("K-a1"), _ref("K-a2", WORKED), _ref("K-a3", WORKED)]
    result = ok(bridge, "nearest", {**args, "committed": committed})
    assert result == {"ref_id": "K-a2", "index": 1, "distance": 0.0, "sum_sq": "0"}
    far = ok(bridge, "nearest", {**args, "committed": [_ref("K-a1")]})
    assert far["ref_id"] == "K-a1" and far["distance"] > 0
    assert Fraction(far["sum_sq"]) > 0


# ---------------------------------------------------------------------------
# Grammar, messages, synthetic books


def test_grammar(bridge: Any) -> None:
    result = ok(bridge, "grammar")
    assert result["families"] == ["K", "Q"] and len(result["atom_ids"]) == 16
    assert len(result["messages"]) == 32
    heldout = [m["message_id"] for m in result["messages"] if m["is_heldout"]]
    assert sorted(heldout) == sorted(HELDOUT_MESSAGE_IDS)
    first = result["messages"][0]
    assert first == {
        "message_id": "K-a1-r1",
        "family": "K",
        "action": "K-a1",
        "referent": "K-r1",
        "status": "Train V1",
        "training_wave": 1,
        "heldout_set": None,
        "is_heldout": False,
    }


def _vector_message(message_id: str, book: int = 0) -> dict[str, Any]:
    data = json.loads(COMPOSITION_VECTORS.read_text("utf-8"))
    return next(m for m in data["books"][book]["messages"] if m["message_id"] == message_id)


def test_compose_trained_message(bridge: Any) -> None:
    args = {"action": _ref("K-a1"), "referent": _ref("K-r1"), "profile": "P1"}
    result = ok(bridge, "compose", {**args, "book_id": "DEMO-P1"})
    expected = _vector_message("K-a1-r1")
    assert result["message_id"] == "K-a1-r1"
    assert result["pcm_sha256"] == expected["composite_sha256"]
    assert result["n_samples"] == expected["n_samples"] == 52_800
    assert result["duration_s"] == 1.1
    assert result["referent_onset"] == result["action_samples"] + 9_600
    data = check_audio(result)
    gap = data[44 + 2 * result["action_samples"] : 44 + 2 * result["referent_onset"]]
    assert gap == bytes(19_200)
    assert ok(bridge, "compose", args)["pcm_sha256"] == result["pcm_sha256"]


def test_compose_refusals(bridge: Any) -> None:
    heldout = {"action": _ref("K-a1"), "referent": _ref("K-r2"), "profile": "P1"}
    error = err(bridge, "compose", heldout)
    assert (error["type"], error["code"]) == ("HeldOutMessageError", "E_HELDOUT")
    swapped = {"action": _ref("K-r1"), "referent": _ref("K-a1"), "profile": "P1"}
    error = err(bridge, "compose", swapped)
    assert (error["type"], error["code"]) == ("CompositionError", "E_ROLE_ORDER")
    families = {"action": _ref("K-a1"), "referent": _ref("Q-r1"), "profile": "P1"}
    assert err(bridge, "compose", families)["code"] == "E_FAMILY_MISMATCH"
    books = {
        "action": _ref("K-a1", book_id="DEMO-A"),
        "referent": _ref("K-r1"),
        "book_id": "DEMO-B",
        "profile": "P1",
    }
    assert err(bridge, "compose", books)["code"] == "E_BOOK_MISMATCH"
    study = {
        "action": _ref("K-a1"),
        "referent": _ref("K-r1"),
        "profile": "P1",
        "book_id": "S-1",
    }
    error = err(bridge, "compose", study)
    assert (error["type"], error["code"]) == ("StoreError", "E_POLICY")
    bad_id = {
        "action": _ref("K-a1") | {"ref_id": "a1"},
        "referent": _ref("K-r1"),
        "profile": "P1",
    }
    assert err(bridge, "compose", bad_id)["type"] == "GrammarError"


def test_composite_hash_allows_heldout_messages_without_audio(bridge: Any) -> None:
    args = {"action": _ref("K-a1"), "referent": _ref("K-r2"), "profile": "P1"}
    result = ok(bridge, "composite_hash", {**args, "book_id": "DEMO-P1"})
    expected = _vector_message("K-a1-r2")
    assert result == {
        "message_id": "K-a1-r2",
        "composite_sha256": expected["composite_sha256"],
        "n_samples": expected["n_samples"],
        "duration_s": expected["n_samples"] / 48_000,
        "is_heldout": True,
    }
    trained = ok(bridge, "composite_hash", {**args, "referent": _ref("K-r1")})
    assert trained["is_heldout"] is False
    assert trained["composite_sha256"] == _vector_message("K-a1-r1")["composite_sha256"]
    swapped = {**args, "action": _ref("K-r2"), "referent": _ref("K-a1")}
    assert err(bridge, "composite_hash", swapped)["code"] == "E_ROLE_ORDER"


def test_synthetic_books_match_the_composition_vectors(bridge: Any) -> None:
    data = json.loads(COMPOSITION_VECTORS.read_text("utf-8"))
    for book in data["books"]:
        result = ok(bridge, "synthetic_book", {"profile": book["profile"]})
        assert result["book_id"] == book["book_id"] == f"DEMO-{book['profile']}"
        assert [
            {k: a[k] for k in ("atom_id", "recipe", "pcm_sha256", "n_samples")}
            for a in book["atoms"]
        ] == result["atoms"]
    assert err(bridge, "synthetic_book", {"profile": "P4"})["type"] == "ValueError"


# ---------------------------------------------------------------------------
# Nonlexical assets and reference checks


def test_nonlexical_assets(bridge: Any) -> None:
    assets = ok(bridge, "nonlexical_list")["assets"]
    assert [a["id"] for a in assets] == [
        "calibration-P1",
        "calibration-P2",
        "calibration-P3",
        "ready-cue",
        "click-action",
        "click-target",
        "click-grammar-demo",
    ]
    registry = json.loads((SOUND_ROOT / "reserved" / "registry.json").read_text("utf-8"))
    by_id = {e["id"]: e for e in registry["entries"]}
    for asset in assets:
        assert asset["pcm_sha256"] == by_id[asset["id"]]["pcm_sha256"]
        assert asset["file_sha256"] == by_id[asset["id"]]["file_sha256"]
        assert asset["peak_dbfs"] < 0 and asset["active_rms_dbfs"] >= asset["rms_dbfs"]
    result = ok(bridge, "nonlexical_get", {"id": "calibration-P1"})
    assert result["n_samples"] == 96_000 and result["profile"] == "P1"
    assert {k: result[k] for k in assets[0]} == assets[0]
    assert len(check_audio(result)) == 44 + 2 * 96_000
    error = err(bridge, "nonlexical_get", {"id": "calibration-P9"})
    assert error["type"] == "KeyError" and "calibration-P9" in error["message"]


def test_vectors_check(bridge: Any) -> None:
    result = ok(bridge, "vectors_check")
    assert result == {
        "renderer": {"checked": 21, "mismatches": []},
        "composition": {"checked": 3 * 16 + 3 * 32 + 4, "mismatches": []},
        "ok": True,
    }


def test_vector_checks_report_mismatches(tmp_path: Path) -> None:
    renderer = json.loads(RENDERER_VECTORS.read_text("utf-8"))
    renderer["vectors"][0]["pcm_sha256"] = "0" * 64
    path = tmp_path / "renderer.json"
    path.write_text(json.dumps(renderer), encoding="utf-8")
    report = bridge_mod.check_renderer_vectors(path)
    assert report["checked"] == 21 and len(report["mismatches"]) == 1
    assert "renderer/spec-worked-example/P1: pcm_sha256: expected '000" in report["mismatches"][0]

    composition = json.loads(COMPOSITION_VECTORS.read_text("utf-8"))
    composition["books"][1]["messages"][3]["composite_sha256"] = "f" * 64
    composition["patterns"][0]["n_samples"] = 1
    path = tmp_path / "composition.json"
    path.write_text(json.dumps(composition), encoding="utf-8")
    report = bridge_mod.check_composition_vectors(path)
    assert len(report["mismatches"]) == 2
    assert report["mismatches"][0].startswith("composition/DEMO-P2/K-a1-r4: composite_sha256")
    assert report["mismatches"][1].startswith("composition/pattern/pattern-shortest: n_samples")


def test_golden_check(bridge: Any) -> None:
    result = ok(bridge, "golden_check")
    manifest = json.loads(GOLDEN_MANIFEST.read_text("utf-8"))
    assert result == {
        "items": len(manifest["items"]),
        "mismatches": [],
        "ok": True,
        "digest": manifest["digests"]["all"],
    }


# ---------------------------------------------------------------------------
# Vocabulary store


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def test_store_flow(bridge: Any) -> None:
    root = Path(ok(bridge, "store_reset")["root"])
    assert root.is_dir() and not inside_work_tree(root)
    assert bridge.temp_root() in root.parents
    created = ok(bridge, "store_create", {"book_id": "DEMO-T1", "profile": "P1"})
    assert created["book_id"] == "DEMO-T1" and _is_sha256(created["chain_head"])
    error = err(bridge, "store_create", {"book_id": "DEMO-T1", "profile": "P1"})
    assert (error["type"], error["code"]) == ("BookExists", "E_BOOK_EXISTS")
    for refused in ("BOOK-1", "demo-x1"):
        error = err(bridge, "store_create", {"book_id": refused, "profile": "P1"})
        assert (error["type"], error["code"]) == ("StoreError", "E_POLICY")

    commit = {"book_id": "DEMO-T1", "atom_id": "K-a1", "semantic_label": "ADD_ONE"}
    first = ok(bridge, "store_commit", {**commit, "recipe": P1["K-a1"]})
    assert first["outcome"] == "commit"
    entry = first["entry"]
    assert entry["atom_id"] == "K-a1" and entry["commit_index"] == 0
    assert entry["recipe"] == P1["K-a1"]
    assert entry["pcm_sha256"] == render(P1["K-a1"], Profile.P1).pcm_sha256
    assert _is_sha256(entry["file_sha256"])
    again = ok(bridge, "store_commit", {**commit, "recipe": P1["K-a1"]})
    assert again["outcome"] == "recommit_noop" and again["entry"] == entry
    assert again["chain_head"] != first["chain_head"]

    error = err(bridge, "store_commit", {**commit, "recipe": P1["K-a2"]})
    assert (error["type"], error["code"]) == ("OverwriteRejected", "E_OVERWRITE")
    assert "details" not in error
    rejected = {**commit, "atom_id": "K-a2", "semantic_label": "FLIP_CARD"}
    error = err(bridge, "store_commit", {**rejected, "recipe": SHORT_EVENT})
    assert (error["type"], error["code"]) == ("CommitRejected", "E_REJECTED")
    assert error["details"]["ok"] is False and error["details"]["codes"] == ["E_EVENT_SHORT"]
    duplicate = err(bridge, "store_commit", {**rejected, "recipe": P1["K-a1"]})
    assert duplicate["details"]["codes"] == ["E_DUPLICATE", "E_SEPARATION"]
    error = err(bridge, "store_commit", {**rejected, "semantic_label": None, "recipe": WORKED})
    assert (error["type"], error["code"]) == ("StoreError", "E_LABEL")
    # An absent label is a bad request (PROTOCOL.md), not the store's E_LABEL for null.
    unlabeled = {k: v for k, v in rejected.items() if k != "semantic_label"}
    error = err(bridge, "store_commit", {**unlabeled, "recipe": WORKED})
    assert (error["type"], error["code"]) == ("ProtocolError", "E_BAD_REQUEST")
    assert "semantic_label" in error["message"]
    # PROTOCOL.md, "store_commit labels": the form is checked first, then the ontology.
    for label in ("x", "add_one", "", "ADD-ONE"):
        error = err(bridge, "store_commit", {**rejected, "semantic_label": label, "recipe": WORKED})
        assert (error["type"], error["code"]) == ("InvalidIdentifier", "E_IDENTIFIER"), label
    for label in ("SCAN", "NOT_A_LABEL", "ADD_ONE"):  # other group, unknown, already bound
        error = err(bridge, "store_commit", {**rejected, "semantic_label": label, "recipe": WORKED})
        assert (error["type"], error["code"]) == ("StoreError", "E_LABEL"), label
    error = err(bridge, "store_commit", {**rejected, "atom_id": "K-x9", "recipe": WORKED})
    assert error["code"] == "E_IDENTIFIER"
    error = err(bridge, "store_commit", {**rejected, "book_id": "DEMO-NONE", "recipe": WORKED})
    assert (error["type"], error["code"]) == ("NotFound", "E_NOT_FOUND")
    second = ok(bridge, "store_commit", {**rejected, "recipe": P1["K-a2"]})
    assert second["entry"]["commit_index"] == 1

    listed = ok(bridge, "store_list", {"book_id": "DEMO-T1"})
    assert [e["atom_id"] for e in listed["entries"]] == ["K-a1", "K-a2"]
    assert listed["entries"][0] == entry and listed["chain_head"] == second["chain_head"]
    assert listed["frozen"] is False and listed["void"] is False
    records = ok(bridge, "store_records", {"book_id": "DEMO-T1"})["records"]
    assert [r["event"] for r in records] == [
        "create_book",
        "commit",
        "recommit_noop",
        "overwrite_rejected",
        "commit",
    ]
    assert all(r["book_id"] == "DEMO-T1" and r["seq"] == i for i, r in enumerate(records))

    frozen_head = ok(bridge, "store_freeze", {"book_id": "DEMO-T1"})["chain_head"]
    assert _is_sha256(frozen_head) and frozen_head != second["chain_head"]
    error = err(bridge, "store_commit", {**commit, "atom_id": "K-a3", "recipe": P1["K-a3"]})
    assert (error["type"], error["code"]) == ("BookFrozen", "E_FROZEN")
    listed = ok(bridge, "store_list", {"book_id": "DEMO-T1"})
    assert listed["frozen"] is True and listed["chain_head"] != frozen_head  # rejection logged
    verified = ok(bridge, "store_verify", {"book_id": "DEMO-T1", "expected_head": frozen_head})
    assert verified == {"ok": True, "issues": []}
    missing = ok(bridge, "store_verify", {"book_id": "DEMO-NONE"})
    assert missing["ok"] is False and missing["issues"][0]["code"] == "E_LOG_MISSING"
    assert missing["issues"][0]["line"] is None  # PROTOCOL.md: null when no log line applies
    for cmd in ("store_list", "store_records", "store_freeze", "store_verify"):
        error = err(bridge, cmd, {"book_id": "STUDY-1"})
        assert (error["type"], error["code"]) == ("StoreError", "E_POLICY")


def _small_book(b: Any, book_id: str) -> str:
    ok(b, "store_reset")
    ok(b, "store_create", {"book_id": book_id, "profile": "P1", "threshold": "0.10"})
    labels = {"K-a1": "ADD_ONE", "K-a2": "REMOVE_ONE"}
    head = ""
    for atom_id, label in labels.items():
        args = {"book_id": book_id, "atom_id": atom_id, "semantic_label": label}
        head = ok(b, "store_commit", {**args, "recipe": P1[atom_id]})["chain_head"]
    return head


@pytest.mark.parametrize(
    ("kind", "codes"),
    [
        ("flip_blob_byte", {"E_BLOB_HASH"}),
        ("edit_log_line", {"E_RECORD_HASH"}),
    ],
)
def test_store_tamper_is_detected(bridge: Any, kind: str, codes: set[str]) -> None:
    head = _small_book(bridge, "DEMO-TAMPER")
    assert ok(bridge, "store_verify", {"book_id": "DEMO-TAMPER"})["ok"] is True
    done = ok(bridge, "store_tamper", {"book_id": "DEMO-TAMPER", "kind": kind})["done"]
    assert isinstance(done, str) and done
    report = ok(bridge, "store_verify", {"book_id": "DEMO-TAMPER", "expected_head": head})
    assert report["ok"] is False
    assert codes <= {i["code"] for i in report["issues"]}
    assert all(set(i) == {"code", "line", "message"} for i in report["issues"])
    # PROTOCOL.md, "Damaged books": every reader and writer refuses the damaged book.
    for cmd, args in (
        ("store_list", {}),
        ("store_records", {}),
        ("store_freeze", {}),
        ("store_commit", {"atom_id": "K-a3", "semantic_label": "FLIP_CARD", "recipe": P1["K-a3"]}),
    ):
        error = err(bridge, cmd, {"book_id": "DEMO-TAMPER", **args})
        assert (error["type"], error["code"]) == ("StoreIntegrityError", "E_INTEGRITY"), cmd


def test_store_truncation_is_detected_against_the_earlier_head(bridge: Any) -> None:
    head = _small_book(bridge, "DEMO-TRUNC")
    done = ok(bridge, "store_tamper", {"book_id": "DEMO-TRUNC", "kind": "truncate_log"})["done"]
    assert "line 2, commit" in done and "self-consistent" in done and "E_ANCHOR" in done
    plain = ok(bridge, "store_verify", {"book_id": "DEMO-TRUNC"})
    assert plain == {"ok": True, "issues": []}  # a shorter log is self-consistent
    listed = ok(bridge, "store_list", {"book_id": "DEMO-TRUNC"})  # so the readers accept it
    assert [e["atom_id"] for e in listed["entries"]] == ["K-a1"]
    assert len(ok(bridge, "store_records", {"book_id": "DEMO-TRUNC"})["records"]) == 2
    anchored = ok(bridge, "store_verify", {"book_id": "DEMO-TRUNC", "expected_head": head})
    assert anchored["ok"] is False and anchored["issues"][0]["code"] == "E_ANCHOR"
    assert anchored["issues"][0]["line"] is None


def _readers_refuse(b: Any, book_id: str) -> None:
    for cmd in ("store_list", "store_records"):
        error = err(b, cmd, {"book_id": book_id})
        assert (error["type"], error["code"]) == ("StoreIntegrityError", "E_INTEGRITY"), cmd


def test_cutting_the_freeze_record_is_detected_without_a_head(fresh: Any) -> None:
    """PROTOCOL.md, "Damaged books": the Store section's flow freezes the book first. The
    cut then removes the freeze record, and its FROZEN marker names no record: the
    readers refuse the book and a plain store_verify reports E_MARKER (line null)."""
    _small_book(fresh, "DEMO-FROZEN")
    frozen_head = ok(fresh, "store_freeze", {"book_id": "DEMO-FROZEN"})["chain_head"]
    done = ok(fresh, "store_tamper", {"book_id": "DEMO-FROZEN", "kind": "truncate_log"})["done"]
    assert "line 3, freeze" in done and "E_MARKER" in done and "E_ANCHOR" not in done
    plain = ok(fresh, "store_verify", {"book_id": "DEMO-FROZEN"})
    assert plain["ok"] is False
    assert [(i["code"], i["line"]) for i in plain["issues"]] == [("E_MARKER", None)]
    _readers_refuse(fresh, "DEMO-FROZEN")
    anchored = ok(fresh, "store_verify", {"book_id": "DEMO-FROZEN", "expected_head": frozen_head})
    assert {i["code"] for i in anchored["issues"]} == {"E_MARKER", "E_ANCHOR"}


def test_a_cut_after_the_freeze_record_is_self_consistent(fresh: Any) -> None:
    """A record logged after the freeze (a refused commit) is cut without touching the
    freeze record: only an earlier head detects that cut."""
    _small_book(fresh, "DEMO-AFTER")
    ok(fresh, "store_freeze", {"book_id": "DEMO-AFTER"})
    args = {"book_id": "DEMO-AFTER", "atom_id": "K-a3", "semantic_label": "FLIP_CARD"}
    assert err(fresh, "store_commit", {**args, "recipe": P1["K-a3"]})["type"] == "BookFrozen"
    head = ok(fresh, "store_list", {"book_id": "DEMO-AFTER"})["chain_head"]
    done = ok(fresh, "store_tamper", {"book_id": "DEMO-AFTER", "kind": "truncate_log"})["done"]
    assert "commit_rejected_frozen" in done and "self-consistent" in done
    assert ok(fresh, "store_verify", {"book_id": "DEMO-AFTER"}) == {"ok": True, "issues": []}
    assert ok(fresh, "store_list", {"book_id": "DEMO-AFTER"})["frozen"] is True
    anchored = ok(fresh, "store_verify", {"book_id": "DEMO-AFTER", "expected_head": head})
    assert [(i["code"], i["line"]) for i in anchored["issues"]] == [("E_ANCHOR", None)]


def test_cutting_the_only_record_leaves_an_empty_log(fresh: Any) -> None:
    ok(fresh, "store_create", {"book_id": "DEMO-ONLY", "profile": "P1"})
    done = ok(fresh, "store_tamper", {"book_id": "DEMO-ONLY", "kind": "truncate_log"})["done"]
    assert "line 0, create_book" in done and "E_EVENT" in done
    plain = ok(fresh, "store_verify", {"book_id": "DEMO-ONLY"})
    assert [(i["code"], i["line"]) for i in plain["issues"]] == [("E_EVENT", None)]
    _readers_refuse(fresh, "DEMO-ONLY")


def test_store_verify_lines_are_numbers_or_null(fresh: Any) -> None:
    head = _small_book(fresh, "DEMO-LINES")
    ok(fresh, "store_tamper", {"book_id": "DEMO-LINES", "kind": "edit_log_line"})
    edited = ok(fresh, "store_verify", {"book_id": "DEMO-LINES", "expected_head": head})
    assert {i["code"]: i["line"] for i in edited["issues"]}["E_RECORD_HASH"] == 2
    for _ in range(3):
        ok(fresh, "store_tamper", {"book_id": "DEMO-LINES", "kind": "truncate_log"})
    empty = ok(fresh, "store_verify", {"book_id": "DEMO-LINES"})
    assert empty["ok"] is False
    assert [(i["code"], i["line"]) for i in empty["issues"]] == [("E_EVENT", None)]


def test_store_tamper_refusals(fresh: Any, tmp_path: Path) -> None:
    error = err(fresh, "store_tamper", {"book_id": "DEMO-X", "kind": "truncate_log"})
    assert (error["type"], error["code"]) == ("StoreError", "E_POLICY")  # no store yet
    _small_book(fresh, "DEMO-X")
    error = err(fresh, "store_tamper", {"book_id": "OTHER-1", "kind": "truncate_log"})
    assert (error["type"], error["code"]) == ("StoreError", "E_POLICY")
    error = err(fresh, "store_tamper", {"book_id": "DEMO-X", "kind": "rm_rf"})
    assert (error["type"], error["code"]) == ("ProtocolError", "E_BAD_REQUEST")
    error = err(fresh, "store_tamper", {"book_id": "DEMO-Y", "kind": "truncate_log"})
    assert (error["type"], error["code"]) == ("NotFound", "E_NOT_FOUND")
    own_root = fresh.store_root

    foreign = VocabularyStore(tmp_path / "foreign")
    foreign.create_book("DEMO-X", "P1", kind="synthetic")
    fresh._store = foreign  # a store that is not the bridge's own temp store
    error = err(fresh, "store_tamper", {"book_id": "DEMO-X", "kind": "truncate_log"})
    assert (error["type"], error["code"]) == ("StoreError", "E_POLICY")
    fresh._store_root = (tmp_path / "foreign").resolve()
    error = err(fresh, "store_tamper", {"book_id": "DEMO-X", "kind": "truncate_log"})
    assert (error["type"], error["code"]) == ("StoreError", "E_POLICY")
    assert foreign.verify("DEMO-X").ok  # untouched
    assert own_root is not None


def test_store_tamper_without_commits(fresh: Any) -> None:
    ok(fresh, "store_create", {"book_id": "DEMO-EMPTY", "profile": "P2"})
    error = err(fresh, "store_tamper", {"book_id": "DEMO-EMPTY", "kind": "flip_blob_byte"})
    assert error["type"] == "ValueError"
    ok(fresh, "store_tamper", {"book_id": "DEMO-EMPTY", "kind": "edit_log_line"})
    report = ok(fresh, "store_verify", {"book_id": "DEMO-EMPTY"})
    assert "E_RECORD_HASH" in {i["code"] for i in report["issues"]}
    ok(fresh, "store_tamper", {"book_id": "DEMO-EMPTY", "kind": "truncate_log"})
    error = err(fresh, "store_tamper", {"book_id": "DEMO-EMPTY", "kind": "truncate_log"})
    assert error["type"] == "ValueError"


def test_close_removes_the_temp_root(tmp_path: Path) -> None:
    b = bridge_mod.Bridge(temp_base=tmp_path)
    _small_book(b, "DEMO-CLOSE")  # read-only blobs and logs
    root = b.temp_root()
    assert root.is_dir() and root.parent == tmp_path.resolve()
    b.close()
    assert not root.exists()
    kept = bridge_mod.Bridge(temp_base=tmp_path, keep_temp=True)
    kept_root = kept.temp_root()
    kept.close()
    assert kept_root.is_dir()
    bridge_mod.remove_tree(kept_root)


# ---------------------------------------------------------------------------
# Fallback and package


def test_fallback_demo(bridge: Any) -> None:
    manifest = json.loads(FALLBACK_MANIFEST.read_text("utf-8"))
    for index, profile in enumerate(("P1", "P2", "P3")):
        result = ok(bridge, "fallback_demo", {"profile": profile})
        assert result["seed_label"] == "DEMO-fallback-v1"
        assert result["fallback_bank_hash"] == manifest["fallback_bank_hash"]
        recorded = manifest["profiles"][index]
        assert len(result["bank"]) == 64 and len(result["book"]) == 16
        assert result["bank"] == [
            {k: e[k] for k in ("index", "recipe", "pcm_sha256")}
            for e in recorded["bank"]["entries"]
        ]
        assert result["book"] == [
            {k: a[k] for k in ("atom_id", "recipe", "pcm_sha256")}
            for a in recorded["book"]["atoms"]
        ]
    assert err(bridge, "fallback_demo", {"profile": "PX"})["type"] == "ValueError"


def test_fallback_scan(bridge: Any) -> None:
    validator = schema_validator("fallback-scan.schema.json")
    empty = ok(bridge, "fallback_scan", {"profile": "P1", "book": []})
    assert empty["outcome"] == "selected" and empty["selected_index"] == 0
    assert empty["threshold"] == "0.1" and not list(validator.iter_errors(empty))
    used = ok(bridge, "fallback_scan", {"profile": "P1", "book": [], "used": [0, 1]})
    assert used["selected_index"] == 2 and used["used"] == [0, 1]
    assert [s["outcome"] for s in used["log"]] == ["used", "used", "selected"]
    book = ok(bridge, "fallback_demo", {"profile": "P1"})["bank"]
    clash = [{"ref_id": "K-a1", "recipe": book[0]["recipe"]}]
    scan = ok(bridge, "fallback_scan", {"profile": "P1", "book": clash})
    assert scan["log"][0]["outcome"] == "rejected" and "E_DUPLICATE" in scan["log"][0]["codes"]
    assert scan["selected_index"] == 1 and scan["reference_ids"] == ["K-a1"]
    error = err(bridge, "fallback_scan", {"profile": "P1", "book": [], "used": [64]})
    assert error["type"] == "ValueError"


def test_package_demo(bridge: Any) -> None:
    result = ok(bridge, "package_demo")
    example = json.loads(EXAMPLE_MANIFEST.read_text("utf-8"))
    assert result["package_sha256"] == example["package_sha256"]
    assert result["files"] == [
        {"path": rel, "sha256": e["sha256"], "bytes": e["bytes"]}
        for rel, e in sorted(example["files"].items())
    ]
    assert result["counts"] == {"atom_wavs": 16, "message_wavs": 18, "heldout_ids": 14}
    assert result["loader_ok"] is True
    assert result["leak_report"]["ok"] is True and result["leak_report"]["findings"] == []
    assert result["leak_report"]["heldout_hashes"] == 14
    assert len(result["answers_preview"]) == 5
    assert result["answers_preview"][0]["message_id"] == "K-a1-r1"
    package_dir = Path(result["dir"])
    assert (package_dir / "manifest.json").is_file() and not inside_work_tree(package_dir)
    assert bridge.temp_root() in package_dir.parents

    # Every request builds again (never cached): same bytes, new directory, and the
    # previous package is removed. A damaged earlier package does not leak into the result.
    (package_dir / "manifest.json").unlink()
    again = ok(bridge, "package_demo")
    assert again["dir"] != result["dir"] and not package_dir.exists()
    assert {k: v for k, v in again.items() if k != "dir"} == {
        k: v for k, v in result.items() if k != "dir"
    }
    assert (Path(again["dir"]) / "manifest.json").is_file()


def test_package_demo_failure_keeps_the_previous_package(
    fresh: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = Path(ok(fresh, "package_demo")["dir"])

    def broken(self: Any, work: Path) -> Any:
        (work / "partial").write_text("x", encoding="utf-8")
        raise RuntimeError("build failed")

    monkeypatch.setattr(bridge_mod.Bridge, "_build_package_demo", broken)
    assert err(fresh, "package_demo")["type"] == "RuntimeError"
    assert first.is_dir()
    assert [p.name for p in fresh.temp_root().glob("package-*")] == [first.parent.name]


def test_reference_checks_reread_their_files(
    bridge: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ok(bridge, "vectors_check")["ok"] is True
    renderer = json.loads(RENDERER_VECTORS.read_text("utf-8"))
    renderer["vectors"][0]["peak"] = -1
    tampered = tmp_path / "renderer.json"
    tampered.write_text(json.dumps(renderer), encoding="utf-8")
    monkeypatch.setattr(bridge_mod, "RENDERER_VECTORS", tampered)
    result = ok(bridge, "vectors_check")
    assert result["ok"] is False and len(result["renderer"]["mismatches"]) == 1

    assert ok(bridge, "golden_check")["ok"] is True
    manifest = json.loads(GOLDEN_MANIFEST.read_text("utf-8"))
    manifest["digests"]["all"] = "0" * 64
    tampered = tmp_path / "manifest.json"
    tampered.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(bridge_mod, "GOLDEN_MANIFEST", tampered)
    result = ok(bridge, "golden_check")
    assert result["ok"] is False and result["mismatches"]


def test_shutdown(fresh: Any) -> None:
    assert ok(fresh, "shutdown") == {}
    assert fresh.closed is True


# ---------------------------------------------------------------------------
# End to end through a subprocess


def _run_bridge(lines: list[bytes]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(BRIDGE_PATH)],
        input=b"".join(line + b"\n" for line in lines),
        capture_output=True,
        timeout=120,
        check=False,
    )


def test_subprocess_round_trip() -> None:
    render_line = json.dumps(
        {"id": 2, "cmd": "render", "args": {"recipe": WORKED, "profile": "P2"}}
    )
    done = _run_bridge(
        [
            b'{"id": 1, "cmd": "hello", "args": {}}',
            b"this is not json",
            render_line.encode(),
            b'{"id": 3, "cmd": "compose", "args": {"profile": "P1"}}',
            b'{"id": 4, "cmd": "store_create", "args": {"book_id": "DEMO-SUB", "profile": "P3"}}',
            b'{"id": 5, "cmd": "shutdown", "args": {}}',
            b'{"id": 6, "cmd": "hello", "args": {}}',
        ]
    )
    assert done.returncode == 0, done.stderr.decode()
    lines = done.stdout.decode("utf-8").splitlines()
    assert len(lines) == 6, "one response per request, nothing after shutdown"
    responses = [json.loads(line) for line in lines]
    assert all(line.startswith('{"id":') for line in lines)  # compact separators
    assert [r["id"] for r in responses] == [1, None, 2, 3, 4, 5]
    assert responses[0]["result"]["bridge_version"] == 1
    assert responses[1]["error"]["code"] == "E_BAD_REQUEST"
    assert check_audio(responses[2]["result"]) and responses[2]["result"]["pcm_sha256"] == (
        WORKED_P2_PCM
    )
    assert responses[3]["error"] == {
        "type": "ProtocolError",
        "code": "E_BAD_REQUEST",
        "message": "missing argument 'action'",
    }
    assert responses[4]["ok"] is True and responses[5] == {
        "id": 5,
        "ok": True,
        "result": {},
    }
    stderr = done.stderr.decode()
    assert "ready" in stderr and "temp root" in stderr
    root = re.search(r"temp root (.+)", stderr)
    assert root is not None and not Path(root.group(1).strip()).exists()  # removed at exit


def test_subprocess_exits_zero_at_end_of_input() -> None:
    done = _run_bridge([b'{"id": 9, "cmd": "grammar"}'])
    assert done.returncode == 0, done.stderr.decode()
    (line,) = done.stdout.splitlines()
    assert json.loads(line)["id"] == 9


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_a_signal_removes_the_temp_root(tmp_path: Path, signum: signal.Signals) -> None:
    """The client escalates to SIGTERM when the bridge does not stop in time."""
    temp = tmp_path / "tmp"
    temp.mkdir()
    process = subprocess.Popen(
        [sys.executable, str(BRIDGE_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "TMPDIR": str(temp)},
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(
            b'{"id":1,"cmd":"store_create","args":{"book_id":"DEMO-SIG","profile":"P1"}}\n'
        )
        process.stdin.write(b'{"id":2,"cmd":"package_demo"}\n')
        process.stdin.flush()
        for expected in (1, 2):
            assert json.loads(process.stdout.readline())["id"] == expected
        (root,) = temp.glob("av-sound-bridge-*")  # store and package, with read-only files
        assert any(not os.access(p, os.W_OK) for p in root.rglob("*") if p.is_file())
        process.send_signal(signum)
        assert process.wait(timeout=30) == 128 + signum
    finally:
        if process.poll() is None:  # pragma: no cover - only when the test fails
            process.kill()
            process.wait()
    assert not root.exists()
    assert list(temp.iterdir()) == []


def test_repeated_signals_do_not_interrupt_the_cleanup(tmp_path: Path) -> None:
    """The same SIGTERM can arrive twice within milliseconds (the app signals uv, which
    forwards it; a process-group signal reaches the bridge too). Signals that arrive
    while the bridge removes its temp root must not cut the removal short."""
    temp = tmp_path / "tmp"
    temp.mkdir()
    process = subprocess.Popen(
        [sys.executable, str(BRIDGE_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "TMPDIR": str(temp)},
    )
    sent = 0
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(
            b'{"id":1,"cmd":"store_create","args":{"book_id":"DEMO-SIG","profile":"P1"}}\n'
        )
        process.stdin.write(b'{"id":2,"cmd":"package_demo"}\n')
        process.stdin.flush()
        for expected in (1, 2):
            assert json.loads(process.stdout.readline())["id"] == expected
        (root,) = temp.glob("av-sound-bridge-*")
        process.stdin.write(b'{"id":3,"cmd":"golden_check"}\n')  # busy when signalled
        process.stdin.flush()
        time.sleep(0.05)
        deadline = time.monotonic() + 30
        while process.poll() is None and time.monotonic() < deadline:
            process.send_signal(signal.SIGTERM if sent % 2 == 0 else signal.SIGHUP)
            sent += 1
            time.sleep(0.001)
        # The first handled signal sets the status (both may be pending at once).
        assert process.wait(timeout=30) in (128 + signal.SIGTERM, 128 + signal.SIGHUP)
    finally:
        if process.poll() is None:  # pragma: no cover - only when the test fails
            process.kill()
            process.wait()
    assert sent >= 2
    assert not root.exists()
    assert list(temp.iterdir()) == []


def test_the_bridge_imports_without_sighup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows has no SIGHUP. The bridge must still import there (the sound workflow
    collects tests/sound on Windows too) and handle the exit signals it does have."""
    assert bridge_mod._EXIT_SIGNALS == (signal.SIGTERM, signal.SIGHUP)
    monkeypatch.delattr(signal, "SIGHUP")
    try:
        module = _load_bridge("av_sound_bridge_nosighup")
        assert module._EXIT_SIGNALS == (signal.SIGTERM,)
    finally:
        sys.modules.pop("av_sound_bridge_nosighup", None)


def test_the_bridge_writes_no_bytecode(tmp_path: Path) -> None:
    """Run as a script, the bridge writes no `__pycache__` for the engine or the tools."""
    prefix = tmp_path / "pycache"  # where Python would put every .pyc it writes
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    done = subprocess.run(
        [sys.executable, str(BRIDGE_PATH)],
        input=b'{"id":1,"cmd":"hello"}\n{"id":2,"cmd":"package_demo"}\n',
        capture_output=True,
        timeout=120,
        check=False,
        env={**env, "PYTHONPYCACHEPREFIX": str(prefix)},
    )
    assert done.returncode == 0, done.stderr.decode()
    assert [json.loads(line)["ok"] for line in done.stdout.splitlines()] == [True, True]
    written = [p.as_posix() for p in prefix.rglob("*.pyc")]
    assert not [p for p in written if "/sound/src/" in p or "/sound/tools/" in p]

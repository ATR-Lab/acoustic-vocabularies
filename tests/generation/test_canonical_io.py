"""Shared hash definitions, torn-tail repair and shared per-file locks (`jsonio`)."""

import hashlib
import threading

import pytest

from av_generation.jsonio import (
    CodecError,
    JsonlAppender,
    canonical_sha256,
    file_set_sha256,
    iter_jsonl,
    messages_sha256,
    path_lock,
    repair_torn_tail,
    schema_sha256,
)

MESSAGES = [
    {"role": "system", "content": "Fixed instruction."},
    {"role": "user", "content": 'Context: {"atom":"K-a1","x":"é"}'},
]


def test_prompt_and_schema_hashes_are_pinned():
    assert messages_sha256(MESSAGES) == canonical_sha256(MESSAGES)
    assert messages_sha256(tuple(dict(m) for m in MESSAGES)) == messages_sha256(MESSAGES)
    assert messages_sha256([{**MESSAGES[0], "name": "x"}, MESSAGES[1]]) == messages_sha256(MESSAGES)
    assert messages_sha256(MESSAGES[::-1]) != messages_sha256(MESSAGES)
    expected = hashlib.sha256(
        b'[{"content":"Fixed instruction.","role":"system"},'
        b'{"content":"Context: {\\"atom\\":\\"K-a1\\",\\"x\\":\\"\\u00e9\\"}","role":"user"}]'
    ).hexdigest()
    assert messages_sha256(MESSAGES) == expected
    schema = {"type": "object", "$id": "x", "properties": {}}
    assert schema_sha256(schema) == canonical_sha256(schema)
    assert schema_sha256({k: v for k, v in schema.items() if k != "$id"}) != schema_sha256(schema)


def test_file_set_hash_ignores_order_and_refuses_platform_paths():
    files = {"b/instruction.txt": "1" * 64, "a.json": "2" * 64}
    assert file_set_sha256(files) == file_set_sha256(dict(reversed(files.items())))
    assert file_set_sha256(files) == canonical_sha256(dict(sorted(files.items())))
    for bad in ({"b\\x.txt": "1" * 64}, {"/abs": "1" * 64}, {"a": "short"}):
        with pytest.raises(CodecError):
            file_set_sha256(bad)


def test_repair_torn_tail(tmp_path):
    path = tmp_path / "slots.jsonl"
    assert repair_torn_tail(path) is None
    path.write_bytes(b'{"a":1}\n{"a":2}\n{"a"')
    with pytest.raises(CodecError):
        list(iter_jsonl(path))
    torn = repair_torn_tail(path)
    assert torn is not None and torn.name == "slots.jsonl"
    assert (torn.offset, torn.n_bytes) == (16, 4)
    assert torn.sha256 == hashlib.sha256(b'{"a"').hexdigest()
    assert [r["a"] for r in iter_jsonl(path)] == [1, 2]
    assert repair_torn_tail(path) is None
    only = tmp_path / "one.jsonl"
    only.write_bytes(b'{"a"')
    assert repair_torn_tail(only).offset == 0 and only.read_bytes() == b""


def test_appenders_of_one_file_share_a_lock(tmp_path):
    path = tmp_path / "logs" / "timing.jsonl"
    first, second = JsonlAppender(path, fsync=False), JsonlAppender(path, fsync=False)
    assert path_lock(path) is path_lock(tmp_path / "logs" / ".." / "logs" / "timing.jsonl")

    def write(app, tag):
        for i in range(200):
            app.append_obj({"tag": tag, "i": i, "pad": "x" * 500})

    threads = [threading.Thread(target=write, args=(a, n)) for n, a in enumerate((first, second))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    rows = list(iter_jsonl(path))
    assert len(rows) == 400
    assert sorted(r["i"] for r in rows if r["tag"] == 0) == list(range(200))

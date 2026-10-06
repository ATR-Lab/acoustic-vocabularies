"""Regenerate the public LFS round-trip fixtures; these are not study stimuli."""
import hashlib
import json
from pathlib import Path
import struct
import wave

root = Path(__file__).resolve().parents[1] / "tests/fixtures"
root.mkdir(parents=True, exist_ok=True)
(root / "triangle.stl").write_bytes(b"solid synthetic\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid synthetic\n")
with wave.open(str(root / "nonlexical.wav"), "wb") as output:
    output.setnchannels(1)
    output.setsampwidth(2)
    output.setframerate(48000)
    output.writeframes(b"".join(struct.pack("<h", 1000 if i == 0 else 0) for i in range(480)))
hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("triangle.stl", "nonlexical.wav")}
(root / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")

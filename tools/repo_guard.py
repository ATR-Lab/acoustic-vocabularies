"""Check committed trees, not the LFS-smudged working directory."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath

BINARY_SUFFIXES = {".stl", ".obj", ".avmesh", ".usd", ".usda", ".usdc", ".usdz", ".glb", ".fbx", ".blend", ".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".psd", ".wav", ".mp3", ".ogg", ".flac", ".mp4", ".zip", ".pdf"}
PRIVATE_DIRS = {"participant-data", "participants", "study-material", "codebooks", "learner-packages", "candidate-banks", "allocation-lists", "confirmatory-seeds", "private", "local-data", "raw-results", "recordings", "external-assets", ".local"}
SECRET_PATTERNS = [
    re.compile(rb"gh[pousr]_" + rb"[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_" + rb"[A-Za-z0-9_]{30,}"),
    re.compile(rb"AKIA" + rb"[A-Z0-9]{16}"),
    re.compile(rb"-----BEGIN " + rb"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:api_key|access_token|password)\s*[:=]\s*[\"']" + rb"[A-Za-z0-9+/=_-]{16,}"),
]
POINTER = re.compile(rb"version https://git-lfs.github.com/spec/v1\noid sha256:[0-9a-f]{64}\nsize [0-9]+\n")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def forbidden_path(path: str) -> bool:
    p = PurePosixPath(path.lower())
    return (any(x in PRIVATE_DIRS for x in p.parts)
            or p.name == ".env" or p.name.startswith(".env.")
            or ".local." in p.name or p.suffix in {".pem", ".key", ".pfx", ".p12"}
            or bool(re.search(r"(?:participant[_-](?:log|id|data)|confirmatory[_-]seed|allocation[_-]list|learner[_-]package|codebook)", p.name)))


def inspect_blob(path: str, data: bytes) -> list[str]:
    errors = []
    if forbidden_path(path):
        errors.append("private path")
    if PurePosixPath(path.lower()).suffix in BINARY_SUFFIXES and not POINTER.fullmatch(data):
        errors.append("binary asset is not an LFS pointer")
    if any(p.search(data) for p in SECRET_PATTERNS):
        errors.append("possible credential; value suppressed")
    return errors


def audit(ref: str = "HEAD") -> list[str]:
    failures = []
    seen = set()
    # All ancestors of this branch, never unrelated proof branches.
    for revision in git("rev-list", ref).decode().splitlines():
        for entry in git("ls-tree", "-r", "-z", revision).split(b"\0"):
            if not entry:
                continue
            header, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = header.split()
            path = raw_path.decode("utf-8", "replace")
            key = (path, oid)
            if key in seen:
                continue
            seen.add(key)
            if kind != b"blob" or mode == b"120000":
                failures.append(f"{path}: submodule/symlink needs explicit security review")
                continue
            for problem in inspect_blob(path, git("cat-file", "blob", oid.decode())):
                failures.append(f"{revision[:12]} {path}: {problem}")
    return failures


if __name__ == "__main__":
    errors = audit(sys.argv[1] if len(sys.argv) > 1 else "HEAD")
    print("\n".join(errors) if errors else "PASS: reachable history, public paths, credentials and LFS pointers")
    raise SystemExit(bool(errors))

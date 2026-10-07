"""Fetch one explicitly approved asset to ignored external-assets storage."""
import argparse
import hashlib
from pathlib import Path
import re
import urllib.parse
import urllib.request


def fetch(url: str, expected: str, output: Path) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Supply a reviewed SHA-256, never a placeholder")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not re.search(r"/(?:resolve|raw)/[0-9a-f]{40}/", parsed.path):
        raise ValueError("Use HTTPS and a full immutable source revision in the URL")
    root = Path(__file__).resolve().parents[1] / "external-assets"
    destination = output.resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError("Output must be inside ignored external-assets/")
    if destination.exists():
        raise FileExistsError("Refusing to replace an existing asset")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("xb") as handle:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(chunk)
                handle.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("SHA-256 mismatch; asset rejected")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print("Verified asset SHA-256:", expected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fetch(args.url, args.sha256, args.output)

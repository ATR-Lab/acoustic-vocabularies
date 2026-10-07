"""Fetch the pinned archive outside version control; never installs software."""
import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import urllib.request
import zipfile

from evidence import sha256


def safe_members(archive, destination):
    destination = destination.resolve()
    for member in archive.infolist():
        name = PurePosixPath(member.filename)
        if name.is_absolute() or ".." in name.parts or "\\" in member.filename:
            raise ValueError("Unsafe archive path")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError("Archive symlinks are not permitted")
        if not (destination / member.filename).resolve().is_relative_to(destination):
            raise ValueError("Archive path escapes destination")
        yield member


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="Ignored *.local directory or external asset cache")
    args = parser.parse_args()
    destination = args.destination.resolve()
    # Force a fresh directory; avoid silently replacing an existing asset set.
    if destination.exists():
        raise SystemExit("Destination already exists; choose a fresh local cache directory")
    pins = json.loads(Path(__file__).with_name("pins.json").read_text())
    destination.mkdir(parents=True)
    archive = destination / "assets.zip.partial"
    url = (f'https://huggingface.co/datasets/{pins["dataset"]}/resolve/'
           f'{pins["dataset_revision"]}/{pins["archive"]}')
    with urllib.request.urlopen(url) as source, archive.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
    if archive.stat().st_size != pins["archive_bytes"] or sha256(archive) != pins["archive_sha256"]:
        raise SystemExit("Archive verification failed; partial retained for inspection")
    final_archive = archive.with_suffix("")
    archive.rename(final_archive)
    with zipfile.ZipFile(final_archive) as source:
        members = list(safe_members(source, destination))
        source.extractall(destination, members=members)
    (destination / "provenance.json").write_text(json.dumps(pins, indent=2) + "\n")
    print("Pinned archive verified and extracted; inspect included notices before use")


if __name__ == "__main__":
    main()

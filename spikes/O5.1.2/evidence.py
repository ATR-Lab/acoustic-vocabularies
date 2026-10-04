"""Dependency-free evidence helpers. No simulator, network, or DDS side effects."""
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_loopback_only(network_root=Path("/sys/class/net")):
    """Fail closed before importing a simulator or middleware module."""
    if not network_root.is_dir():
        raise RuntimeError("Linux network namespace evidence unavailable")
    interfaces = sorted(p.name for p in network_root.iterdir())
    if interfaces != ["lo"]:
        raise RuntimeError("Run inside a --network none container; only lo is allowed")
    return interfaces


def revision(root):
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def require_revision(root, expected):
    if revision(root) != expected:
        raise RuntimeError("Checkout does not match pins.json")
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip()
    if dirty:
        raise RuntimeError("Pinned source has tracked modifications")


def public_name(path, roots):
    """Only logical roots enter public evidence, never machine absolute paths."""
    path = Path(path).resolve()
    for name, root in roots.items():
        try:
            return name + "/" + path.relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    raise ValueError("Unmapped evidence path: add a logical root before publication")


def hash_files(paths, output, roots):
    rows = []
    for path in sorted({Path(p).resolve() for p in paths}, key=str):
        rows.append({"path": public_name(path, roots), "sha256": sha256(path),
                     "bytes": path.stat().st_size})
    write_csv(output, ["path", "sha256", "bytes"], rows)
    return rows


def write_csv(path, columns, rows):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def stats(values):
    values = sorted(float(v) for v in values)
    if not values:
        return {"count": 0, "mean": None, "p95": None, "max": None}
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Nonfinite measurements")
    return {"count": len(values), "mean": statistics.mean(values),
            "p95": values[max(0, math.ceil(.95 * len(values)) - 1)], "max": values[-1]}


def summarize(directory):
    directory = Path(directory)
    with (directory / "resources.csv").open(newline="") as stream:
        resource_rows = list(csv.DictReader(stream))
    with (directory / "steps.csv").open(newline="") as stream:
        steps = list(csv.DictReader(stream))
    result = {key: stats(row[key] for row in resource_rows if row.get(key)) for key in
              ("gpu_total_used_mib", "process_rss_mib", "cpu_one_core_percent")}
    result["step_wall_ms"] = stats(row["step_wall_ms"] for row in steps)
    result["render_interval_ms"] = stats(row["render_interval_ms"] for row in steps
                                         if row.get("render_interval_ms"))
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    print(json.dumps(summarize(parser.parse_args().run_directory), indent=2))

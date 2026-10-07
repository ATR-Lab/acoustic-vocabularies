"""Read-only host inventory. Outputs public-safe hardware/software facts only."""
import argparse
import importlib.metadata
import json
import platform
from pathlib import Path
import subprocess


def command(args):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=20)
        return {"exit_code": result.returncode, "stdout": result.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": type(exc).__name__}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os_release = platform.freedesktop_os_release() if platform.system() == "Linux" else {}
    result = {
        "os": os_release.get("PRETTY_NAME", platform.system()),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "gpu": command(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
                        "--format=csv,noheader,nounits"]),
        "ram": command(["free", "--bytes"]),
        "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                     if any(s in d.metadata.get("Name", "").lower() for s in
                            ("isaac", "unitree", "cyclone", "torch", "websocket"))},
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()

"""Explicitly synthetic transport smoke server. Never evidence of live Isaac timing."""
import argparse
import json
import math
from pathlib import Path
import time
from publishers import CustomPublisher, RosPublisher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=("custom", "rosbridge"), required=True)
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--rate", type=int, choices=(30, 60), default=30)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--joints", type=Path, required=True, help="JSON array in #46 canonical order")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = json.loads(args.joints.read_text())
    publisher = CustomPublisher(args.socket, names, "synthetic") if args.candidate == "custom" else RosPublisher(names, "synthetic")
    start, next_tick, step = time.monotonic(), time.monotonic(), 0
    cpu = time.process_time()
    try:
        while time.monotonic() - start < args.seconds:
            now = time.monotonic()
            if now >= next_tick:
                publisher.publish(now - start, step, [0.05 * math.sin(now - start)] * len(names))
                step += 1
                next_tick += 1 / args.rate
                if next_tick < now:
                    next_tick = now + 1 / args.rate  # do not burst catch-up frames
            time.sleep(.001)
    finally:
        publisher.close()
    elapsed = time.monotonic() - start
    args.output.write_text(json.dumps({"source_kind": "synthetic", "frames": step,
        "elapsed_s": elapsed, "process_cpu_s": time.process_time() - cpu,
        "server_queue_overwrites": getattr(publisher, "overwrites", None)}, indent=2) + "\n")


if __name__ == "__main__":
    main()

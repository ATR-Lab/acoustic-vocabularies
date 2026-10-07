"""Engineering-only command schedules and input/legibility summaries (standard library)."""
import argparse
import csv
import json
import math
from pathlib import Path
import random
import statistics

METHODS = ("controller-ray", "hand-poke")
TARGETS = "ABCDEFGH"


def legal(target, action):
    return target in TARGETS and len(target) == 1 and action in actions(target)


def actions(target):
    if target not in TARGETS or len(target) != 1:
        return []
    family = "tray" if target in "ABCD" else "container"
    return [f"{family}-{i}" for i in range(1, 5)]


def schedule(tester_count=3):
    if tester_count < 3:
        raise ValueError("At least 3 internal engineering testers required")
    result = []
    for index in range(tester_count):
        for method in METHODS[::1 if index % 2 == 0 else -1]:
            commands = [(target, action) for target in TARGETS for action in actions(target)]
            random.SystemRandom().shuffle(commands)
            for order, (target, action) in enumerate(commands):
                result.append({"trial_id": str(order + 1), "tester_code": f"T{index+1:02d}",
                               "method": method, "target": target, "action": action})
    return {"trials": result, "material": "public engineering placeholders; no study vocabulary"}


def quantile(values, q):
    if not values:
        return None
    data = sorted(values)
    position = (len(data) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return data[lo] + (data[hi] - data[lo]) * (position - lo)


def analyze(events, annotations):
    groups = {}
    for event in events:
        if event["method"] not in METHODS:
            raise ValueError("Unknown method")
        if not math.isfinite(float(event["host_s"])):
            raise ValueError("Non-finite timestamp")
        key = tuple(event[k] for k in ("tester_code", "method", "trial_id", "attempt"))
        groups.setdefault(key, []).append(event)
    annotation_map = {}
    for row in annotations:
        key = tuple(row[k] for k in ("tester_code", "method", "trial_id", "attempt"))
        if key in annotation_map or row["accidental_commit"] not in ("true", "false"):
            raise ValueError("Annotations need unique attempts and explicit true/false")
        annotation_map[key] = row
    trials = []
    for key, rows in groups.items():
        times = [float(row["host_s"]) for row in rows]
        if times != sorted(times):
            raise ValueError("Events must retain monotonic order within each attempt")
        prompts = [r for r in rows if r["event"] == "prompt"]
        commits = [r for r in rows if r["event"] == "commit"]
        if not prompts:
            continue  # idle tracking/resume events are still counted in method summary
        if len(prompts) != 1 or len(commits) > 1:
            raise ValueError("Duplicate prompt/commit in one attempt")
        prompt = prompts[0]
        if not legal(prompt["prompt_target"], prompt["prompt_action"]):
            raise ValueError("Illegal engineering prompt")
        loss = any(r["event"] in ("input_lost", "focus_lost", "paused") for r in rows)
        commit = commits[0] if commits else None
        elapsed = float(commit["host_s"]) - float(prompt["host_s"]) if commit else None
        if elapsed is not None and elapsed < 0:
            raise ValueError("Commit precedes prompt")
        if commit and not legal(commit["target"], commit["action"]):
            raise ValueError("Harness exported an illegal committed tuple")
        selection_rows = [r for r in rows if r["event"] in ("target", "action")]
        wrong = sum((r["target"] != prompt["prompt_target"] if r["event"] == "target"
                     else r["action"] != prompt["prompt_action"]) for r in selection_rows)
        annotation = annotation_map.get(key)
        trials.append(dict(zip(("tester_code", "method", "trial_id", "attempt"), key),
            prompt_target=prompt["prompt_target"], prompt_action=prompt["prompt_action"],
            complete=commit is not None, input_loss=loss, elapsed_s=elapsed,
            selection_count=len(selection_rows), wrong_selections=wrong,
            wrong_commit=(commit["target"] != prompt["prompt_target"] or commit["action"] != prompt["prompt_action"]) if commit else None,
            accidental_commit=annotation["accidental_commit"] == "true" if annotation and commit else None))
    summary = {}
    for method in METHODS:
        subset = [r for r in trials if r["method"] == method]
        completed = [r for r in subset if r["complete"] and not r["input_loss"]]
        durations = [r["elapsed_s"] for r in completed]
        selections = sum(r["selection_count"] for r in subset)
        coverage = {}
        for row in completed:
            coverage.setdefault(row["tester_code"], set()).add((row["prompt_target"], row["prompt_action"]))
        all_covered = len(coverage) >= 3 and all(len(commands) == 32 for commands in coverage.values())
        p95 = quantile(durations, .95)
        observed_commits = [r for r in subset if r["complete"]]
        annotated = all(r["accidental_commit"] is not None for r in observed_commits) and bool(observed_commits)
        method_events = [e for e in events if e["method"] == method]
        losses, recoveries, open_loss = [], [], None
        for event in sorted(method_events, key=lambda e: (e["tester_code"], float(e["host_s"]))):
            identity = event["tester_code"]
            if event["event"] == "input_lost":
                losses.append(event)
                open_loss = (identity, float(event["host_s"]))
            elif event["event"] == "input_recovered" and open_loss and open_loss[0] == identity:
                recoveries.append(float(event["host_s"]) - open_loss[1])
                open_loss = None
        summary[method] = {"attempts": len(subset), "completed_without_loss": len(completed),
            "median_s": statistics.median(durations) if durations else None, "p95_s": p95,
            "wrong_selection_count": sum(r["wrong_selections"] for r in subset),
            "selection_count": selections,
            "wrong_selection_rate": sum(r["wrong_selections"] for r in subset) / selections if selections else None,
            "wrong_commit_count": sum(bool(r["wrong_commit"]) for r in observed_commits),
            "accidental_commit_count": sum(r["accidental_commit"] is True for r in observed_commits) if annotated else None,
            "annotation_complete": annotated, "tracking_loss_events": len(losses),
            "recovery_median_s": statistics.median(recoveries) if recoveries else None,
            "recovery_p95_s": quantile(recoveries, .95), "losses_with_recovery": len(recoveries),
            "coverage_per_tester": {code: len(commands) for code, commands in coverage.items()},
            "three_testers_all_commands": all_covered,
            "speed_screen_met": all_covered and p95 is not None and p95 <= 3.5,
            "recommendation": "pending device checks, annotated errors, legibility and comfort review"}
    return trials, summary


def legibility(rows, tester_codes, margin=1.5):
    """Smallest angle with all explicitly required testers, no errors, >=8 labels."""
    if len(set(tester_codes)) < 3 or margin < 1:
        raise ValueError("Need >=3 specified tester codes and margin >=1")
    groups = {}
    for row in rows:
        angle = float(row["angle_deg"])
        tested, errors = int(row["labels_tested"]), int(row["errors"])
        if not math.isfinite(angle) or angle <= 0 or errors < 0 or tested < errors:
            raise ValueError("Invalid legibility observation")
        groups.setdefault(angle, {}).setdefault(row["tester_code"], []).append((tested, errors))
    valid = [angle for angle, testers in groups.items() if all(
        code in testers and sum(n for n, _ in testers[code]) >= 8 and all(e == 0 for _, e in testers[code])
        for code in tester_codes)]
    minimum = min(valid) if valid else None
    return {"minimum_all_read_deg": minimum, "margin": margin,
            "candidate_panel_text_deg": minimum * margin if minimum else None,
            "status": "candidate must be rechecked on final panel" if minimum else "incomplete"}


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    schedule_parser = sub.add_parser("schedule")
    schedule_parser.add_argument("output", type=Path)
    schedule_parser.add_argument("--testers", type=int, default=3)
    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("--events", type=Path, nargs="+", required=True)
    analyze_parser.add_argument("--annotations", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path, required=True)
    ladder = sub.add_parser("legibility")
    ladder.add_argument("input", type=Path)
    ladder.add_argument("--tester-codes", nargs="+", required=True)
    args = parser.parse_args()
    if args.command == "schedule":
        args.output.write_text(json.dumps(schedule(args.testers), indent=2) + "\n", encoding="utf-8")
    elif args.command == "legibility":
        print(json.dumps(legibility(read_csv(args.input), args.tester_codes), indent=2))
    else:
        events = [row for path in args.events for row in read_csv(path)]
        trials, result = analyze(events, read_csv(args.annotations))
        args.output.mkdir(parents=True, exist_ok=True)
        if trials:
            with (args.output / "trials.csv").open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=list(trials[0]))
                writer.writeheader(); writer.writerows(trials)
        (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

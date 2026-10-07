"""Station onset timing of the rater panel (#21): logged onsets and loopback captures.

One table, two sources:

- **logged**: the host's `play` records (`plays.jsonl`): each station's estimate of its
  audio onset on the server clock (`onset_ms`) against the scheduled time
  (`scheduled_ms`). Criterion: candidate onset within `panel.ONSET_TOLERANCE_MS` (50 ms)
  of the slot start and reference onset within 50 ms of 2.0 s, on every station.
- **loopback**: one multichannel recording of the stations' headphone outputs, one
  channel per station, on one audio interface (one sample clock). Onsets are detected per
  channel (`detect_onsets`) and mapped to the server clock with one linear fit to the
  session schedule (`panel-schedule.json` written by `panel_demo`), so a constant
  recorder delay and a recorder clock-rate difference drop out. The station-to-station
  skew (max - min onset of one scheduled sound) does not depend on that mapping.
  Criterion: skew <= `panel.MAX_SKEW_MS` (100 ms) over 36 consecutive slots (one atom).

Rows are scheduled audio onsets (slot x role) in schedule order; CSV columns
`ROW_COLUMNS` + one `onset_ms_<station>` column per station. Command line:

    uv run --project generation python -m av_generation.panel_skew logged \
        --plays <run>/logs/plays.jsonl --schedule <run>/panel-schedule.json --out <dir>
    uv run --project generation python -m av_generation.panel_skew loopback \
        --capture capture.wav --channels S1,S2,S3 --schedule <run>/panel-schedule.json --out <dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from av_generation.constants import REFERENCE_ONSET_MS
from av_generation.panel import MAX_SKEW_MS, ONSET_TOLERANCE_MS
from av_generation.records import PlayEvent, read_records

FloatArray = npt.NDArray[np.float64]

ROLES: Final[tuple[str, ...]] = ("candidate", "reference")
REQUIRED_CONSECUTIVE_SLOTS: Final = 36
MATCH_WINDOW_MS: Final = 250.0
MIN_GAP_MS: Final = 300.0
THRESHOLD_BELOW_PEAK_DB: Final = 30.0
THRESHOLD_ABOVE_NOISE: Final = 4.0
ROW_COLUMNS: Final[tuple[str, ...]] = (
    "index",
    "rating_slot_id",
    "role",
    "scheduled_ms",
    "skew_ms",
    "max_abs_dev_ms",
    "within_skew",
    "within_onset",
)


class SkewInputError(ValueError):
    """A schedule, play log or capture that the analysis cannot use."""


@dataclass(frozen=True, slots=True)
class ScheduledOnset:
    """One scheduled audio onset of a session."""

    slot_index: int
    """0-based slot index in schedule order."""
    rating_slot_id: str
    role: str
    scheduled_ms: int


@dataclass(frozen=True, slots=True)
class Schedule:
    """The audio onsets of a panel session (`panel-schedule.json`)."""

    run_id: str
    stations: tuple[str, ...]
    slot_ids: tuple[str, ...]
    onsets: tuple[ScheduledOnset, ...]
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class OnsetRow:
    """One scheduled onset with the measured onset of every station (server clock)."""

    index: int
    slot_index: int
    rating_slot_id: str
    role: str
    scheduled_ms: int
    onsets: Mapping[str, float | None]

    @property
    def complete(self) -> bool:
        return all(v is not None for v in self.onsets.values())

    @property
    def skew_ms(self) -> float | None:
        values = [v for v in self.onsets.values() if v is not None]
        return max(values) - min(values) if len(values) >= 2 else None

    @property
    def max_abs_dev_ms(self) -> float | None:
        values = [abs(v - self.scheduled_ms) for v in self.onsets.values() if v is not None]
        return max(values) if values else None

    @property
    def within_skew(self) -> bool:
        skew = self.skew_ms
        return self.complete and skew is not None and skew <= MAX_SKEW_MS

    @property
    def within_onset(self) -> bool:
        dev = self.max_abs_dev_ms
        return self.complete and dev is not None and dev <= ONSET_TOLERANCE_MS


@dataclass(frozen=True, slots=True)
class Alignment:
    """Capture time -> server time: `server = (capture - offset_ms) / scale`."""

    scale: float
    offset_ms: float
    matched: int
    detections: int

    @property
    def rate_ppm(self) -> float:
        return (self.scale - 1.0) * 1e6

    def to_server(self, capture_ms: float) -> float:
        return (capture_ms - self.offset_ms) / self.scale


# ---------------------------------------------------------------------------
# Inputs


def schedule_from_document(data: Mapping[str, Any], *, sha256: str | None = None) -> Schedule:
    """Parse a `panel-schedule.json` document (`panel_demo.schedule_document`)."""
    if data.get("format") != "av-generation/panel-schedule" or data.get("format_version") != 1:
        raise SkewInputError("not an av-generation/panel-schedule v1 document")
    onsets: list[ScheduledOnset] = []
    slot_ids: list[str] = []
    for index, slot in enumerate(sorted(data["slots"], key=lambda s: int(s["start_ms"]))):
        slot_ids.append(str(slot["rating_slot_id"]))
        if slot["candidate"] is not None:
            onsets.append(
                ScheduledOnset(index, slot["rating_slot_id"], "candidate", int(slot["start_ms"]))
            )
        if slot["reference"] is not None:
            onsets.append(
                ScheduledOnset(
                    index,
                    slot["rating_slot_id"],
                    "reference",
                    int(slot["start_ms"]) + REFERENCE_ONSET_MS,
                )
            )
    return Schedule(
        str(data["run_id"]), tuple(data["stations"]), tuple(slot_ids), tuple(onsets), sha256
    )


def read_schedule(path: str | Path) -> Schedule:
    raw = Path(path).read_bytes()
    return schedule_from_document(json.loads(raw), sha256=hashlib.sha256(raw).hexdigest())


def schedule_from_plays(plays: Sequence[PlayEvent], run_id: str) -> Schedule:
    """A schedule reconstructed from play records (when no schedule file exists)."""
    seen: dict[tuple[str, str], int] = {}
    for p in plays:
        if p.rating_slot_id is not None and p.scheduled_ms is not None:
            role = "candidate" if p.context == "rating_candidate" else "reference"
            seen[(p.rating_slot_id, role)] = p.scheduled_ms
    slot_start: dict[str, int] = {}
    for (slot_id, role), t in seen.items():
        start = t - (REFERENCE_ONSET_MS if role == "reference" else 0)
        slot_start[slot_id] = min(start, slot_start.get(slot_id, start))
    order = sorted(slot_start, key=lambda s: (slot_start[s], s))
    index = {s: i for i, s in enumerate(order)}
    onsets = sorted(
        (ScheduledOnset(index[s], s, r, t) for (s, r), t in seen.items()),
        key=lambda o: (o.scheduled_ms, o.role),
    )
    stations = tuple(sorted({p.station for p in plays if p.station is not None}))
    return Schedule(run_id, stations, tuple(order), tuple(onsets))


def logged_onsets(plays: Iterable[PlayEvent]) -> dict[str, dict[tuple[str, str], float]]:
    """station -> (rating_slot_id, role) -> logged onset (first `played` record)."""
    out: dict[str, dict[tuple[str, str], float]] = {}
    for p in plays:
        if p.result != "played" or p.context not in ("rating_candidate", "rating_reference"):
            continue
        if p.station is None or p.rating_slot_id is None or p.onset_ms is None:
            continue
        role = "candidate" if p.context == "rating_candidate" else "reference"
        out.setdefault(p.station, {}).setdefault((p.rating_slot_id, role), float(p.onset_ms))
    return out


@dataclass(frozen=True, slots=True)
class Capture:
    """A multichannel recording: `samples[channel]` as float64 in [-1, 1]."""

    rate: int
    samples: FloatArray
    sha256: str | None = None


_PCM = 1
_FLOAT = 3
_EXTENSIBLE = 0xFFFE


def parse_wav(data: bytes) -> Capture:
    """Decode RIFF/WAVE PCM (8/16/24/32-bit), IEEE float (32/64-bit) or
    WAVE_FORMAT_EXTENSIBLE of either, any channel count."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise SkewInputError("not a RIFF/WAVE file")
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        chunk = data[pos + 8 : pos + 8 + size]
        if cid == b"fmt ":
            fmt = chunk
        elif cid == b"data":
            body = chunk
        pos += 8 + size + (size & 1)
    if fmt is None or body is None or len(fmt) < 16:
        raise SkewInputError("WAVE file without fmt or data chunk")
    tag, channels, rate, _, align, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == _EXTENSIBLE:
        if len(fmt) < 40:
            raise SkewInputError("short WAVE_FORMAT_EXTENSIBLE header")
        tag = struct.unpack("<H", fmt[24:26])[0]
    width = bits // 8
    if channels < 1 or width < 1 or align != channels * width:
        raise SkewInputError(f"unsupported WAVE layout: {channels} channels, {bits} bits")
    frames = len(body) // align
    raw = np.frombuffer(body[: frames * align], dtype=np.uint8).reshape(frames, channels, width)
    if tag == _FLOAT and width in (4, 8):
        dtype = np.dtype("<f4") if width == 4 else np.dtype("<f8")
        values = raw.reshape(frames, channels * width).copy().view(dtype).astype(np.float64)
    elif tag == _PCM and width == 1:
        values = (raw[..., 0].astype(np.float64) - 128.0) / 128.0
    elif tag == _PCM and width in (2, 3, 4):
        acc = np.zeros((frames, channels), dtype=np.int64)
        for b in range(width):
            acc |= raw[..., b].astype(np.int64) << (8 * b)
        sign = 1 << (bits - 1)
        acc = (acc ^ sign) - sign
        values = acc.astype(np.float64) / float(sign)
    else:
        raise SkewInputError(f"unsupported WAVE format tag {tag} with {bits} bits")
    return Capture(int(rate), np.ascontiguousarray(values.T), hashlib.sha256(data).hexdigest())


def read_capture(path: str | Path) -> Capture:
    return parse_wav(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Onsets and alignment


def detect_onsets(
    signal: npt.ArrayLike,
    rate: int,
    *,
    threshold: float | None = None,
    min_gap_ms: float = MIN_GAP_MS,
) -> FloatArray:
    """Onset times (ms from the first sample) of the sounds in one channel.

    An onset is the first sample whose magnitude reaches `threshold` after at least
    `min_gap_ms` below it. The default threshold is the larger of 30 dB below the
    channel's peak and 4x the median of the 1-ms block peaks (the noise floor of a
    mostly silent recording). Gaps inside one atom are at most 60 ms, so one atom gives
    one onset."""
    x = np.abs(np.asarray(signal, dtype=np.float64))
    if x.size == 0 or not np.isfinite(x).all():
        return np.zeros(0)
    peak = float(x.max())
    if peak <= 0.0:
        return np.zeros(0)
    if threshold is None:
        block = max(1, rate // 1000)
        n_blocks = x.size // block
        noise = (
            float(np.median(x[: n_blocks * block].reshape(n_blocks, block).max(axis=1)))
            if n_blocks
            else 0.0
        )
        threshold = max(peak * 10 ** (-THRESHOLD_BELOW_PEAK_DB / 20), noise * THRESHOLD_ABOVE_NOISE)
    idx = np.flatnonzero(x >= threshold)
    if idx.size == 0:
        return np.zeros(0)
    gap = max(1, int(round(min_gap_ms * rate / 1000)))
    starts = idx[np.concatenate(([True], np.diff(idx) > gap))]
    return np.asarray(starts * (1000.0 / rate), dtype=np.float64)


def _nearest(
    sorted_values: FloatArray, targets: FloatArray
) -> tuple[npt.NDArray[np.int64], FloatArray]:
    pos = np.clip(np.searchsorted(sorted_values, targets), 1, max(1, sorted_values.size - 1))
    left = sorted_values[pos - 1]
    right = sorted_values[np.minimum(pos, sorted_values.size - 1)]
    use_left = np.abs(targets - left) <= np.abs(targets - right)
    index = np.where(use_left, pos - 1, np.minimum(pos, sorted_values.size - 1))
    return index.astype(np.int64), np.abs(targets - sorted_values[index])


def align(
    scheduled_ms: Sequence[float],
    detected_ms: Mapping[str, FloatArray],
    *,
    window_ms: float = MATCH_WINDOW_MS,
) -> Alignment:
    """Fit `capture = scale * server + offset` from detected onsets to scheduled ones.

    A coarse search tries the shifts between the first detections and the first scheduled
    onsets and keeps the one that matches most detections within `window_ms`; two
    least-squares passes over the matched pairs then fit offset and scale."""
    sched = np.sort(np.asarray(scheduled_ms, dtype=np.float64))
    pooled = np.sort(
        np.concatenate([np.asarray(v, dtype=np.float64) for v in detected_ms.values()])
    )
    if sched.size == 0 or pooled.size == 0:
        raise SkewInputError("nothing to align: no scheduled onsets or no detections")
    best: tuple[int, float, float] | None = None
    for d in pooled[:16]:
        for e in sched[:16]:
            shift = float(d - e)
            _, dist = _nearest(sched, pooled - shift)
            ok = dist <= window_ms
            key = (int(ok.sum()), -float(dist[ok].sum()) if ok.any() else 0.0, shift)
            if best is None or key[:2] > best[:2]:
                best = key
    assert best is not None
    scale, offset = 1.0, best[2]
    matched = 0
    for _ in range(3):
        idx, dist = _nearest(sched, (pooled - offset) / scale)
        ok = dist <= window_ms
        matched = int(ok.sum())
        if matched >= 2 and np.ptp(sched[idx[ok]]) > 0:
            scale, offset = (float(v) for v in np.polyfit(sched[idx[ok]], pooled[ok], 1))
        elif matched >= 1:
            offset = float(np.median(pooled[ok] - sched[idx[ok]]))
    return Alignment(scale, offset, matched, int(pooled.size))


def capture_onsets(
    capture: Capture,
    schedule: Schedule,
    channels: Mapping[str, int],
    *,
    window_ms: float = MATCH_WINDOW_MS,
) -> tuple[dict[str, dict[tuple[str, str], float]], Alignment]:
    """Detected onsets per station mapped to the server clock and matched to the schedule."""
    detected: dict[str, FloatArray] = {}
    for station, channel in channels.items():
        if not 0 <= channel < capture.samples.shape[0]:
            raise SkewInputError(f"{station}: channel {channel + 1} is not in the capture")
        detected[station] = detect_onsets(capture.samples[channel], capture.rate)
    alignment = align([o.scheduled_ms for o in schedule.onsets], detected, window_ms=window_ms)
    out: dict[str, dict[tuple[str, str], float]] = {}
    for station, times in detected.items():
        server = (times - alignment.offset_ms) / alignment.scale
        found: dict[tuple[str, str], float] = {}
        if server.size:
            for onset in schedule.onsets:
                i = int(np.argmin(np.abs(server - onset.scheduled_ms)))
                if abs(server[i] - onset.scheduled_ms) <= window_ms:
                    found[(onset.rating_slot_id, onset.role)] = float(server[i])
        out[station] = found
    return out, alignment


# ---------------------------------------------------------------------------
# Table and summary


def onset_table(
    schedule: Schedule,
    onsets: Mapping[str, Mapping[tuple[str, str], float]],
    *,
    stations: Sequence[str] | None = None,
    max_slots: int | None = None,
) -> list[OnsetRow]:
    """One row per scheduled onset (in the first `max_slots` slots), every station."""
    names = tuple(stations) if stations is not None else schedule.stations
    rows: list[OnsetRow] = []
    for onset in schedule.onsets:
        if max_slots is not None and onset.slot_index >= max_slots:
            continue
        key = (onset.rating_slot_id, onset.role)
        rows.append(
            OnsetRow(
                index=len(rows) + 1,
                slot_index=onset.slot_index,
                rating_slot_id=onset.rating_slot_id,
                role=onset.role,
                scheduled_ms=onset.scheduled_ms,
                onsets={s: onsets.get(s, {}).get(key) for s in names},
            )
        )
    return rows


def longest_run(rows: Sequence[OnsetRow], n_slots: int, *, attr: str) -> int:
    """Longest run of consecutive slots whose rows all pass `attr` (slots without audio,
    i.e. placeholders, pass)."""
    failed = {r.slot_index for r in rows if not getattr(r, attr)}
    best = run = 0
    for index in range(n_slots):
        run = 0 if index in failed else run + 1
        best = max(best, run)
    return best


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def summarize(
    rows: Sequence[OnsetRow],
    schedule: Schedule,
    *,
    source: str,
    stations: Sequence[str],
    max_slots: int | None = None,
    alignment: Alignment | None = None,
    capture_sha256: str | None = None,
) -> dict[str, Any]:
    """Summary of a table: skew and onset statistics and the pass/fail of both criteria."""
    n_slots = min(len(schedule.slot_ids), max_slots) if max_slots else len(schedule.slot_ids)
    skews = np.asarray([r.skew_ms for r in rows if r.skew_ms is not None], dtype=np.float64)
    devs = np.asarray(
        [r.max_abs_dev_ms for r in rows if r.max_abs_dev_ms is not None], dtype=np.float64
    )
    by_station: dict[str, Any] = {}
    for station in stations:
        dev = [abs(v - r.scheduled_ms) for r in rows if (v := r.onsets.get(station)) is not None]
        pairs: dict[str, dict[str, float]] = {}
        for r in rows:
            value = r.onsets.get(station)
            if value is not None:
                pairs.setdefault(r.rating_slot_id, {})[r.role] = value
        interval = [
            abs(p["reference"] - p["candidate"] - REFERENCE_ONSET_MS)
            for p in pairs.values()
            if "candidate" in p and "reference" in p
        ]
        by_station[station] = {
            "detected": sum(1 for r in rows if r.onsets.get(station) is not None),
            "max_abs_dev_ms": _round(max(dev)) if dev else None,
            "max_reference_interval_error_ms": _round(max(interval)) if interval else None,
        }
    skew_run = longest_run(rows, n_slots, attr="within_skew")
    onset_run = longest_run(rows, n_slots, attr="within_onset")
    summary: dict[str, Any] = {
        "source": source,
        "run_id": schedule.run_id,
        "schedule_sha256": schedule.sha256,
        "capture_sha256": capture_sha256,
        "stations": list(stations),
        "n_slots": n_slots,
        "n_rows": len(rows),
        "complete_rows": sum(r.complete for r in rows),
        "skew_limit_ms": MAX_SKEW_MS,
        "onset_tolerance_ms": ONSET_TOLERANCE_MS,
        "max_skew_ms": _round(skews.max()) if skews.size else None,
        "p95_skew_ms": _round(np.percentile(skews, 95)) if skews.size else None,
        "mean_skew_ms": _round(skews.mean()) if skews.size else None,
        "max_abs_dev_ms": _round(devs.max()) if devs.size else None,
        "rows_within_skew": sum(r.within_skew for r in rows),
        "rows_within_onset": sum(r.within_onset for r in rows),
        "longest_slot_run_within_skew": skew_run,
        "longest_slot_run_within_onset": onset_run,
        "pass_skew_36_slots": skew_run >= REQUIRED_CONSECUTIVE_SLOTS,
        "pass_onset_all_rows": bool(rows) and all(r.within_onset for r in rows),
        "by_station": by_station,
        "alignment": None
        if alignment is None
        else {
            "offset_ms": _round(alignment.offset_ms, 3),
            "rate_ppm": _round(alignment.rate_ppm, 2),
            "matched": alignment.matched,
            "detections": alignment.detections,
        },
    }
    if source == "loopback":
        summary["note"] = (
            "onsets are mapped to the server clock by one linear fit to the schedule; "
            "max_abs_dev_ms is relative to that fit, the skew is not affected by it"
        )
    return summary


def write_table(rows: Sequence[OnsetRow], path: str | Path, stations: Sequence[str]) -> None:
    """The onset table as CSV (`ROW_COLUMNS` + `onset_ms_<station>`)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow([*ROW_COLUMNS, *(f"onset_ms_{s}" for s in stations)])
        for r in rows:
            writer.writerow(
                [
                    r.index,
                    r.rating_slot_id,
                    r.role,
                    r.scheduled_ms,
                    "" if r.skew_ms is None else f"{r.skew_ms:.1f}",
                    "" if r.max_abs_dev_ms is None else f"{r.max_abs_dev_ms:.1f}",
                    int(r.within_skew),
                    int(r.within_onset),
                    *("" if (v := r.onsets.get(s)) is None else f"{v:.1f}" for s in stations),
                ]
            )


def write_summary(summary: Mapping[str, Any], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Command line


def main(argv: Sequence[str] | None = None) -> int:
    """Onset tables of a panel session: `logged` (play records) or `loopback` (capture)."""
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.panel_skew", description=main.__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)
    logged = sub.add_parser("logged", help="station-logged onsets from plays.jsonl")
    logged.add_argument("--plays", type=Path, required=True)
    logged.add_argument("--schedule", type=Path, help="panel-schedule.json (default: from plays)")
    loop = sub.add_parser("loopback", help="onsets detected in a loopback capture")
    loop.add_argument("--capture", type=Path, required=True)
    loop.add_argument("--schedule", type=Path, required=True)
    loop.add_argument("--channels", required=True, help="stations in channel order, e.g. S1,S2,S3")
    for p in (logged, loop):
        p.add_argument("--out", type=Path, required=True, help="output directory")
        p.add_argument("--slots", type=int, default=None, help="only the first N slots")
    args = parser.parse_args(argv)

    if args.command == "logged":
        plays = list(read_records(args.plays, PlayEvent))
        if args.schedule is not None:
            schedule = read_schedule(args.schedule)
        else:
            run_id = plays[0].run_id if plays else "unknown"
            schedule = schedule_from_plays(plays, run_id)
        stations = schedule.stations
        rows = onset_table(schedule, logged_onsets(plays), stations=stations, max_slots=args.slots)
        summary = summarize(
            rows, schedule, source="logged", stations=stations, max_slots=args.slots
        )
        name = "logged-onsets.csv"
    else:
        schedule = read_schedule(args.schedule)
        stations = tuple(s.strip() for s in args.channels.split(",") if s.strip())
        capture = read_capture(args.capture)
        onsets, alignment = capture_onsets(
            capture, schedule, {s: i for i, s in enumerate(stations)}
        )
        rows = onset_table(schedule, onsets, stations=stations, max_slots=args.slots)
        summary = summarize(
            rows,
            schedule,
            source="loopback",
            stations=stations,
            max_slots=args.slots,
            alignment=alignment,
            capture_sha256=capture.sha256,
        )
        name = "loopback-skew.csv"
    write_table(rows, args.out / name, stations)
    write_summary(summary, args.out / name.replace(".csv", "-summary.json"))
    print(
        f"{name}: {len(rows)} onsets, max skew {summary['max_skew_ms']} ms, "
        f"max |dev| {summary['max_abs_dev_ms']} ms, "
        f"skew criterion {'met' if summary['pass_skew_36_slots'] else 'not met'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

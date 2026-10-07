"""Secondary native trace checks. None asserts physical presentation timing."""
from __future__ import annotations

from collections import defaultdict

from .records import exact, guid, hash_value, number, require, strict


def operator(chains, schedule_hash):
    accepted, incomplete = [], set()
    for rows in chains:
        requests, results = {}, set()
        nonce = rows[0]["session_nonce"] if rows else None
        for row in rows:
            p = row["record"]
            exact(p, "kind request host_mono_ms previous_consumed_sequence stop_supersedes_through_sequence prior_receipt_acknowledgement receipt")
            q = p["request"]
            exact(q, "version session_nonce request_id sequence command run_sheet_manifest_sha256 schedule_sha256")
            require(type(q["version"]) is int and q["version"] == 1 and q["session_nonce"] == nonce == row["session_nonce"]
                    and guid(q["request_id"]) and type(q["sequence"]) is int and q["sequence"] > 0
                    and q["schedule_sha256"] == schedule_hash and hash_value(q["run_sheet_manifest_sha256"]), "MOCK_OPERATOR_BINDING")
            require(q["command"] in {"load", "start", "pause", "resume", "stop"}, "MOCK_OPERATOR_COMMAND")
            require(type(p["previous_consumed_sequence"]) is int and p["previous_consumed_sequence"] >= 0, "MOCK_OPERATOR_SEQUENCE")
            stop = q["command"] == "stop"
            require(p["stop_supersedes_through_sequence"] == (q["sequence"] - 1 if stop else None)
                    and p["prior_receipt_acknowledgement"] == ("unknown" if stop else None), "MOCK_OPERATOR_STOP")
            key = q["request_id"]
            if p["kind"] == "request":
                require(key not in requests and p["receipt"] is None, "MOCK_OPERATOR_DUPLICATE_REQUEST")
                requests[key] = p
            elif p["kind"] == "result":
                require(key in requests and key not in results and q == requests[key]["request"]
                        and p["previous_consumed_sequence"] == requests[key]["previous_consumed_sequence"], "MOCK_OPERATOR_RESULT_ORDER")
                receipt = p["receipt"]
                exact(receipt, "request_id sequence status code")
                require(receipt["request_id"] == key and receipt["sequence"] == q["sequence"]
                        and receipt["status"] in {"accepted", "rejected"} and isinstance(receipt["code"], str), "MOCK_OPERATOR_RECEIPT")
                results.add(key)
                if receipt["status"] == "accepted":
                    require(q["sequence"] == p["previous_consumed_sequence"] + 1 or stop and q["sequence"] > p["previous_consumed_sequence"], "MOCK_OPERATOR_SEQUENCE")
                    accepted.append(q["command"])
            else: require(False, "MOCK_OPERATOR_RECORD_KIND")
        if set(requests) != results: incomplete.add("OPERATOR_REQUEST_WITHOUT_RESULT")
    if "load" not in accepted or "start" not in accepted: incomplete.add("OPERATOR_LOAD_START_MISSING")
    return incomplete


def display(joined, attempts, items, study, visit):
    views, incomplete = defaultdict(list), set()
    for row in joined:
        if row["kind"] != "view": continue
        p = row["payload"]
        exact(p, "kind attempt_id phase observed_mono_ms text_sha256 visible evidence_level")
        require(p["kind"] == "assessment_view_command" and p["evidence_level"] == "native_view_command_not_physical_capture"
                and hash_value(p["text_sha256"]) and number(p["observed_mono_ms"]) and type(p["visible"]) is bool, "MOCK_VIEW_RECORD")
        require(p["attempt_id"] is None or p["attempt_id"] in attempts, "MOCK_VIEW_ATTEMPT")
        require(p["phase"] in {"protected_neutral", "acknowledgment", "tail_end", "forms_complete"}
                or isinstance(p["phase"], str) and p["phase"].startswith("rating_"), "MOCK_VIEW_PHASE")
        views[p["attempt_id"]].append(p)
    ack_hashes = set()
    for aid, a in attempts.items():
        item = items[aid]
        if item["block"] not in {"pre_old", "trained", "novel", "atomic", "validity"}: continue
        events = views[aid]
        phases = [v["phase"] for v in events]
        if not all(p in phases for p in ("protected_neutral", "acknowledgment", "tail_end")):
            incomplete.add("PROTECTED_DISPLAY_TRACE_INCOMPLETE"); continue
        require(phases.count("acknowledgment") == phases.count("tail_end") == 1, "MOCK_VIEW_DUPLICATE")
        ack = next(v for v in events if v["phase"] == "acknowledgment")
        tail = next(v for v in events if v["phase"] == "tail_end")
        anchor = a["first"]["payload"]["scheduled_onset_mono_ms"]
        close = 7 if item["block"] == "atomic" else 12
        require(ack["observed_mono_ms"] + 1e-6 >= anchor + close * 1000
                and tail["observed_mono_ms"] + 1e-6 >= anchor + item["slot_s"] * 1000
                and ack["observed_mono_ms"] <= tail["observed_mono_ms"], "MOCK_ACK_TAIL_EARLY")
        ack_hashes.add(ack["text_sha256"])
    require(len(ack_hashes) <= 1, "MOCK_ACK_TARGET_DEPENDENT_TEXT")
    global_phases = {v["phase"] for v in views[None]}
    if "forms_complete" not in global_phases: incomplete.add("FORMS_DISPLAY_TRACE_INCOMPLETE")
    terminal = [r["payload"] for r in joined if r["kind"] == "module" and r["payload"].get("kind") == "native_run_end"]
    if not terminal: incomplete.add("NATIVE_RUN_END_MISSING")
    else:
        for p in terminal:
            exact(p, "kind status complete scope participant_admission")
            require(p["scope"] == "SIMULATION_TEST" and p["participant_admission"] is False and type(p["complete"]) is bool, "MOCK_RUN_END_SCOPE")
        require(len(terminal) == 1, "MOCK_RUN_END_DUPLICATE")
        # This row is a pre-cleanup intent. The separate, mandatory native_result
        # artifact binds actual teardown/export success to the process receipt.
        if terminal[-1]["status"] != "JOIN_COMPLETE_FORMS_RECORDED":
            incomplete.add("NATIVE_RUN_END_INCOMPLETE")
    return incomplete


def lesson(joined, attempts, items, requests):
    seen, incomplete = defaultdict(list), set()
    for row in joined:
        if row["kind"] != "lesson": continue
        p = row["payload"]
        exact(p, "kind attempt_id opportunity_id audio_request_id presentation_index observed_mono_ms expected_mono_ms meaning_display_id feedback_content_id highlight pcm_sha256 action_pcm_sha256 referent_pcm_sha256")
        require(p["attempt_id"] in attempts and p["opportunity_id"] == p["attempt_id"] and number(p["observed_mono_ms"]), "MOCK_LESSON_CONTEXT")
        require(items[p["attempt_id"]]["block"] in {"atomic_lessons", "message_lessons"}, "MOCK_LESSON_OUTSIDE_TEACHING")
        if p["audio_request_id"] is not None:
            require(p["audio_request_id"] in attempts[p["attempt_id"]]["first"]["payload"]["audio_request_ids"], "MOCK_LESSON_AUDIO_ID")
            if p["pcm_sha256"] is not None:
                request = requests.get(p["audio_request_id"])
                if request is None and p["kind"] == "play_request":
                    incomplete.add("LESSON_AUDIO_REQUEST_MISSING")
                else:
                    require(request is not None and p["pcm_sha256"] == request["payload"]["pcm_sha256"], "MOCK_LESSON_PCM")
        seen[p["attempt_id"]].append(p)
    for aid in attempts:
        if not items[aid]["block"].endswith("lessons"): continue
        rows = seen[aid]
        if not rows: incomplete.add("LESSON_DISPLAY_TRACE_MISSING"); continue
        for kind, expected in (("play_request",3),("onset_authority",3),("play_complete",3),("display_start",2),("display_end",2),("retrieval_opportunity",1),("lesson_end",1)):
            actual = [r for r in rows if r["kind"] == kind]
            require(len(actual) <= expected, "MOCK_LESSON_DUPLICATE_EVENT")
            if len(actual) != expected: incomplete.add("LESSON_EVENT_COVERAGE_INCOMPLETE")
            if kind in {"play_request", "onset_authority", "play_complete"}:
                require([r["presentation_index"] for r in actual] == list(range(1, len(actual)+1)), "MOCK_LESSON_PLAY_ORDER")
        for r in rows:
            if r["kind"] in {"display_start", "display_end", "retrieval_opportunity", "lesson_end"}:
                require(number(r["expected_mono_ms"]), "MOCK_LESSON_DISPLAY_BOUNDARY")
                if not -1e-6 <= r["observed_mono_ms"] - r["expected_mono_ms"] <= 20: incomplete.add("LESSON_SOFTWARE_DISPLAY_TIMING_FAILED")
            if r["kind"] == "onset_authority":
                q = requests[r["audio_request_id"]]["payload"]
                require(abs(r["expected_mono_ms"] - q["software_output_estimate_mono_ms"]) <= 1e-6, "MOCK_LESSON_OUTPUT_ANCHOR")
    return incomplete


def grammar(rows, requests):
    missing, active, complete, phases = set(), False, False, []
    ids, observed, finished = {}, set(), set()
    for row in rows:
        p = row["payload"]; kind, key = p["kind"], p["audio_request_id"]
        if kind == "started":
            require(not active and not complete, "MOCK_GRAMMAR_DUPLICATE_START"); active = True
        elif kind == "request":
            require(active and not complete and key not in ids and key not in requests and phases == ([] if p["phase"] == "ready" else ["ready"]), "MOCK_GRAMMAR_PLAY_ORDER")
            if phases: require(next(iter(ids)) in finished, "MOCK_GRAMMAR_OVERLAP")
            ids[key] = p["phase"]; phases.append(p["phase"])
        elif kind == "audio":
            require(key in ids and p["phase"] == ids[key], "MOCK_GRAMMAR_OBSERVATION_CONTEXT")
            a = p["audio"]
            if a["code"] == "SIMULATION_DELIVERY_OBSERVED":
                require(key not in observed and a["callback_count"] > 0 and a["delivered_samples"] > 0, "MOCK_GRAMMAR_CALLBACK")
                observed.add(key)
            if a["code"] == "AUDIO_PLAYBACK_COMPLETED":
                require(key in observed and key not in finished and a["callback_count"] > 0 and a["delivered_samples"] > 0, "MOCK_GRAMMAR_COMPLETION")
                finished.add(key)
        elif kind == "completed": require(key in finished, "MOCK_GRAMMAR_COMPLETION")
        elif kind == "finished":
            require(active and not complete and phases == ["ready","clicks"] and set(ids) == finished, "MOCK_GRAMMAR_FINISH")
            complete = True
        elif kind == "interrupted": missing.add("GRAMMAR_INTERRUPTED")
    if not complete: missing.add("GRAMMAR_COMPLETION_MISSING")
    return missing


def menu(chains, attempts, items, requests, role, schedule_hash, package_hash):
    incomplete, seen, seals = set(), set(), 0
    retired_interruptions = set()
    for chain in chains:
        require(chain and chain[0]["record"].get("kind") == "header", "MOCK_MENU_HEADER")
        header = chain[0]["record"]
        exact(header, "kind format binding created_utc clock_epoch")
        binding = header["binding"]
        exact(binding, "package_sha256 bank_sha256 allocation_sha256 schedule_sha256 unit_binding_sha256 review_sha256 visit role menu_keys")
        require(header["format"] == "av-menu-ledger/1" and guid(header["clock_epoch"])
                and binding["package_sha256"] == package_hash and binding["schedule_sha256"] == schedule_hash and binding["role"] == role, "MOCK_MENU_BINDING")
        require(isinstance(binding["menu_keys"],list) and len(set(binding["menu_keys"])) == len(binding["menu_keys"]), "MOCK_MENU_KEYS")
        events = defaultdict(list)
        for row in chain[1:]:
            p = row["record"]
            if p.get("kind") in {"sealed", "sealed_yoked"}:
                exact(p, "kind menu_count" if p["kind"] == "sealed" else "kind menu_count active_ledger_sha256 yoked_event_sha256")
                require(row is chain[-1] and type(p["menu_count"]) is int and p["menu_count"] == len(events) == len(binding["menu_keys"]), "MOCK_MENU_SEAL")
                require(p["kind"] == ("sealed_yoked" if role == "yoked" else "sealed"), "MOCK_MENU_SEAL_ROLE")
                seals += 1; continue
            exact(p, "kind event_id attempt_id opportunity_id menu_key meaning_display_id slot_start_mono_ms mono_ms expected_mono_ms onset_uncertainty_ms audio_request_id presentation_index candidate_id pcm_sha256 file_sha256 yoked_source_event_id selected_index defaulted phase matching_deviation_id receipt_sha256")
            require(guid(p["event_id"]) and p["event_id"] not in seen, "MOCK_MENU_EVENT_ID"); seen.add(p["event_id"])
            aid = p["attempt_id"]
            require(aid in attempts and p["opportunity_id"] == aid and items[aid]["block"] in {"profile_menu", "atom_menus"}, "MOCK_MENU_ATTEMPT")
            require(p["kind"] in {"menu_start","choice_revised","choice_final","selection_verified","display_request","display_changed","play_request","onset_authority","play_complete","menu_interrupted"}, "MOCK_MENU_EVENT_KIND")
            require(p["menu_key"] == ("profile" if items[aid]["block"] == "profile_menu" else items[aid]["atom_id"])
                    and p["menu_key"] in binding["menu_keys"], "MOCK_MENU_CONTENT_KEY")
            if p["slot_start_mono_ms"] != attempts[aid]["first"]["payload"]["scheduled_onset_mono_ms"]:
                matches = [(index, c) for index, c in enumerate(attempts[aid].get("retired_contexts", []))
                           if p["slot_start_mono_ms"] == c["first"]["payload"]["scheduled_onset_mono_ms"]]
                require(len(matches) == 1, "MOCK_MENU_ANCHOR")
                index, context = matches[0]
                require((aid, index) not in retired_interruptions and aid not in events and p["kind"] == "menu_interrupted"
                        and p["matching_deviation_id"] == p["event_id"]
                        and all(p[k] is None for k in ("expected_mono_ms", "onset_uncertainty_ms", "audio_request_id",
                            "presentation_index", "candidate_id", "pcm_sha256", "file_sha256", "yoked_source_event_id",
                            "selected_index", "defaulted", "phase", "receipt_sha256")), "MOCK_MENU_RETIRED_CONTENT")
                require(number(p["mono_ms"]) and _loaded_time(context) <= p["mono_ms"] < p["slot_start_mono_ms"]
                        and p["mono_ms"] < context["resume"]["payload"]["host_mono_ms"]
                        and not any(r["payload"]["state"] == "CueRequested" or r["payload"]["exposure_consumed"] for r in context["records"]),
                        "MOCK_MENU_RETIRED_BOUNDARY")
                retired_interruptions.add((aid, index))
                incomplete.update({"MENU_EVENTS_INCOMPLETE", "MENU_RETIRED_PLANNING_INTERRUPTION"})
                continue
            if role == "yoked" and p["kind"] != "menu_interrupted": require(guid(p["yoked_source_event_id"]), "MOCK_YOKED_SOURCE_EVENT")
            if p["audio_request_id"] is not None:
                require(p["audio_request_id"] in attempts[aid]["first"]["payload"]["audio_request_ids"], "MOCK_MENU_AUDIO_ID")
                q = requests.get(p["audio_request_id"])
                if q and p["pcm_sha256"] is not None: require(p["pcm_sha256"] == q["payload"]["pcm_sha256"], "MOCK_MENU_PCM")
            events[aid].append(p)
        if not chain[-1]["record"].get("kind", "").startswith("sealed"): incomplete.add("MENU_LEDGER_UNSEALED")
        if set(events) != {aid for aid in attempts if items[aid]["block"] in {"profile_menu","atom_menus"}}: incomplete.add("MENU_ATTEMPT_COVERAGE_INCOMPLETE")
        for aid, rows in events.items():
            counts = {k: sum(r["kind"] == k for r in rows) for k in ("menu_start", "choice_final", "selection_verified", "play_request", "play_complete")}
            require(counts["menu_start"] == 1 and counts["choice_final"] <= 1 and counts["play_request"] <= 8 and counts["play_complete"] <= 8, "MOCK_MENU_EVENT_COUNT")
            if counts["choice_final"] != 1 or counts["selection_verified"] != 1 or counts["play_request"] != 8 or counts["play_complete"] != 8: incomplete.add("MENU_EVENTS_INCOMPLETE")
            for r in rows:
                if r["kind"] == "choice_final":
                    seconds = 45 if items[aid]["block"] == "profile_menu" else 32
                    require(abs(r["expected_mono_ms"] - (r["slot_start_mono_ms"] + seconds * 1000)) <= 1e-6, "MOCK_MENU_CHOICE_DEADLINE")
                if r["kind"] == "onset_authority":
                    q=requests.get(r["audio_request_id"])
                    require(q is not None and number(r["onset_uncertainty_ms"]) and r["onset_uncertainty_ms"] <= 20
                            and abs(r["expected_mono_ms"] - q["payload"]["software_output_estimate_mono_ms"]) <= 1e-6, "MOCK_MENU_OUTPUT_ANCHOR")
            for kind in ("play_request","onset_authority","play_complete"):
                rr=[r for r in rows if r["kind"]==kind]
                require([r["presentation_index"] for r in rr] == list(range(1,len(rr)+1)), "MOCK_MENU_PLAY_ORDER")
                if len(rr)!=8: incomplete.add("MENU_EVENTS_INCOMPLETE")
            displays=[r for r in rows if r["kind"]=="display_changed"]
            phases=["Instructions","Audition","Choice","Selected","Neutral","Ended"]
            require([r["phase"] for r in displays] == phases[:len(displays)] and len(displays)<=6, "MOCK_MENU_DISPLAY_ORDER")
            if len(displays)!=6: incomplete.add("MENU_DISPLAY_COVERAGE_INCOMPLETE")
            if role=="active":
                bounds=[0,6000,30000,45000,58000,60000] if items[aid]["block"]=="profile_menu" else [0,4000,22000,32000,40000,45000]
                if any(abs(r["mono_ms"]-r["slot_start_mono_ms"]-offset)>20 for r,offset in zip(displays,bounds)): incomplete.add("MENU_DISPLAY_TIMING_SCREEN_FAILED")
    require(not retired_interruptions or not seals, "MOCK_MENU_RETIRED_SEAL")
    if not seals: incomplete.add("MENU_SEAL_MISSING")
    if role == "yoked": incomplete.add("ACTIVE_LEDGER_COMPARISON_REQUIRED")
    return incomplete


def compare_yoked(active, yoked, active_file_hash):
    """Compare full visit timing, including between-menu pauses, across clocks."""
    a_bind, y_bind = active[0]["record"]["binding"], yoked[0]["record"]["binding"]
    require(a_bind["role"] == "active" and y_bind["role"] == "yoked", "MOCK_YOKED_ROLES")
    for key in ("package_sha256", "bank_sha256", "allocation_sha256", "unit_binding_sha256", "review_sha256", "visit", "menu_keys"):
        require(a_bind[key] == y_bind[key], "MOCK_YOKED_UNIT_BINDING")
    require(active[-1]["record"]["kind"] == "sealed" and yoked[-1]["record"]["kind"] == "sealed_yoked"
            and yoked[-1]["record"]["active_ledger_sha256"] == active_file_hash, "MOCK_YOKED_SEAL_PIN")
    a = {r["record"]["event_id"]:r["record"] for r in active[1:-1]}
    grouped = defaultdict(list)
    for row in a.values(): grouped[row["menu_key"]].append(row)
    a_start = min(r["slot_start_mono_ms"] for r in a.values())
    y_rows = [r["record"] for r in yoked[1:-1]]
    require(y_rows, "MOCK_YOKED_EMPTY")
    y_start = min(r["slot_start_mono_ms"] for r in y_rows)
    compared = 0
    for row in y_rows:
        require(row["kind"] != "choice_revised" and row["yoked_source_event_id"] in a, "MOCK_YOKED_SOURCE_EVENT")
        source = a[row["yoked_source_event_id"]]
        require(row["menu_key"] == source["menu_key"] and row["meaning_display_id"] == source["meaning_display_id"], "MOCK_YOKED_CONTENT")
        require(abs((row["slot_start_mono_ms"] - y_start) - (source["slot_start_mono_ms"] - a_start)) <= 1e-6, "MOCK_YOKED_PAUSE_MISMATCH")
        if row["kind"] in {"play_request", "onset_authority", "play_complete"}:
            require(source["kind"] == "play_request" and all(row[k] == source[k] for k in ("presentation_index","candidate_id","pcm_sha256","file_sha256")), "MOCK_YOKED_AUDIO_CONTENT")
            onsets = [r for r in grouped[row["menu_key"]] if r["kind"] == "onset_authority" and r["presentation_index"] == row["presentation_index"]]
            require(len(onsets) == 1, "MOCK_YOKED_SOURCE_ONSET")
            if row["kind"] in {"play_request", "onset_authority"}:
                offset = onsets[0]["expected_mono_ms"] - source["slot_start_mono_ms"]
                uncertainty = row["onset_uncertainty_ms"] if row["kind"] == "onset_authority" else 0
                require(number(uncertainty) and uncertainty <= 20 and abs(row["expected_mono_ms"] - row["slot_start_mono_ms"] - offset) <= uncertainty + 1e-6, "MOCK_YOKED_OUTPUT_TIMING")
        elif row["kind"] in {"display_request","display_changed"}:
            require(source["kind"] == row["kind"] and source["phase"] == row["phase"], "MOCK_YOKED_DISPLAY_SOURCE")
            displays=[r for r in grouped[row["menu_key"]] if r["kind"] == "display_changed" and r["phase"] == row["phase"]]
            require(len(displays)==1 and abs(row["mono_ms"] - row["slot_start_mono_ms"] - (displays[0]["mono_ms"] - source["slot_start_mono_ms"])) <= 20, "MOCK_YOKED_DISPLAY_TIMING")
        elif row["kind"] in {"choice_final","selection_verified"}:
            require(source["kind"] == row["kind"] and all(row[k] == source[k] for k in ("selected_index","defaulted","receipt_sha256")), "MOCK_YOKED_SELECTION")
        else: require(row["kind"] == source["kind"] == "menu_start", "MOCK_YOKED_EVENT_KIND")
        compared += 1
    return {"visit":a_bind["visit"],"events_compared":compared,"active_ledger_sha256":active_file_hash,
            "software_trace_timing_matched":True,"acoustic_timing_qualified":False}


def _loaded_time(context):
    rows = [r["payload"] for r in context["records"]
            if r["payload"]["event"] == "state_after" and r["payload"]["state"] == "Loaded"]
    require(len(rows) == 1 and number(rows[0]["host_mono_ms"]), "MOCK_FRAME_CONTEXT_LOADED")
    return rows[0]["host_mono_ms"]


def frames(root, entries, attempts, table, read, relative):
    incomplete, summaries, interval_count, maximum = set(), set(), 0, 0.0
    summary_counts, retired_verified = defaultdict(int), []
    for artifact, raw in entries:
        parent = root / relative(artifact["path"])
        m = strict(raw)
        exact(m, "version capture_kind files")
        require(type(m["version"]) is int and m["version"] == 1 and m["capture_kind"] == "application_render_callbacks_not_photon_timestamps", "MOCK_FRAME_KIND")
        files = {}
        require(isinstance(m["files"], list) and 3 <= len(m["files"]) <= 4, "MOCK_FRAME_FILES")
        for f in m["files"]:
            exact(f, "path bytes sha256")
            require(f["path"] in {"metadata.json", "frames.csv", "events.jsonl", "runtime-rate.json"} and f["path"] not in files, "MOCK_FRAME_FILE")
            files[f["path"]] = read(parent.parent / f["path"], f["sha256"])
            require(type(f["bytes"]) is int and len(files[f["path"]]) == f["bytes"], "MOCK_FRAME_SIZE")
        require({"metadata.json", "frames.csv", "events.jsonl"} <= files.keys(), "MOCK_FRAME_FILES")
        metadata = strict(files["metadata.json"])
        nominal_rate = metadata.get("selected_refresh_hz")
        require(number(nominal_rate) and nominal_rate > 0 and metadata.get("physical_qualification") is False, "MOCK_FRAME_METADATA")
        if any(a.get("retired_contexts") for a in attempts.values()):
            # The integrated engine and capture both use AudioPlayer.Now. Their
            # component epoch IDs differ; an arbitrary clock cannot be joined.
            require(metadata.get("clock") == "Unity_process_Stopwatch_ms" and guid(metadata.get("clock_epoch")), "MOCK_FRAME_CONTEXT_CLOCK")
            require(all(c["first"]["payload"]["clock_epoch"] == c["replacement"]["payload"]["clock_epoch"]
                        for a in attempts.values() for c in a.get("retired_contexts", [])), "MOCK_FRAME_CONTEXT_CLOCK")
        rows = table(files["frames.csv"])
        grouped = defaultdict(list)
        for r in rows:
            aid = r["attempt_id"]
            require(aid in attempts and r["opportunity_id"] == aid, "MOCK_FRAME_ATTEMPT")
            values = [float(r[k]) for k in ("from_mono_ms", "to_mono_ms", "render_interval_ms", "overlap_ms", "runtime_refresh_hz")]
            require(all(number(v) for v in values), "MOCK_FRAME_NUMBER")
            start, end, gap, overlap, rate = values
            require(end > start and abs((end - start) - gap) <= 1e-6 and 0 < overlap <= gap + 1e-6 and rate > 0, "MOCK_FRAME_INTERVAL")
            require(r["window_kind"] in {"cue", "response"}, "MOCK_FRAME_WINDOW")
            if attempts[aid].get("retired_contexts"):
                # The physical render interval may start before registration
                # during a stall. Only its attributed overlap must be within
                # the current window; the full interval still counts as a gap.
                current = attempts[aid]["first"]["payload"]
                require((r["window_kind"] == "response" and r["window_id"] == "response") or
                        (r["window_kind"] == "cue" and r["window_id"] in current["audio_request_ids"]), "MOCK_FRAME_CONTEXT_WINDOW")
                require(end > current["scheduled_onset_mono_ms"]
                        and overlap <= end-max(start, current["scheduled_onset_mono_ms"])+1e-6, "MOCK_FRAME_RETIRED_INTERVAL")
            grouped[aid].append((int(r["frame_index"]), gap, rate, end)); interval_count += 1; maximum = max(maximum, gap)
        require(not files["events.jsonl"] or files["events.jsonl"].endswith(b"\n"), "MOCK_FRAME_TORN_EVENTS")
        previous_stamp = -1
        for line in files["events.jsonl"].splitlines():
            p = strict(line)
            aid, stamp = p.get("attempt_id"), p.get("observed_mono_ms")
            require(aid in attempts and p.get("opportunity_id") == aid and number(stamp) and stamp >= previous_stamp, "MOCK_FRAME_EVENT_ORDER")
            previous_stamp = stamp
            if p.get("kind") == "fault":
                exact(p, "kind attempt_id opportunity_id observed_mono_ms technical_fault_code render_gap_ms watchdog")
                if attempts[aid].get("retired_contexts"):
                    require(stamp >= _loaded_time(attempts[aid]), "MOCK_FRAME_RETIRED_STARTED")
                incomplete.add("FRAME_FAULT_PRESENT"); continue
            exact(p, "kind attempt_id opportunity_id observed_mono_ms frame_freeze_ms frame_count within_1_5x_count capture_complete cancelled_before_window")
            require(p["kind"] == "summary" and aid not in summaries, "MOCK_FRAME_SUMMARY")
            require(type(p["capture_complete"]) is bool and type(p["cancelled_before_window"]) is bool
                    and type(p["frame_count"]) is int and type(p["within_1_5x_count"]) is int, "MOCK_FRAME_SUMMARY_TYPES")
            retired = attempts[aid].get("retired_contexts", [])
            index = summary_counts[aid]
            if index < len(retired):
                context = retired[index]; first = context["first"]["payload"]
                require(p["cancelled_before_window"] is True and p["capture_complete"] is False
                        and p["frame_count"] == p["within_1_5x_count"] == 0 and p["frame_freeze_ms"] is None,
                        "MOCK_FRAME_RETIRED_STARTED")
                require(_loaded_time(context) <= stamp < first["scheduled_onset_mono_ms"]
                        and stamp < context["resume"]["payload"]["host_mono_ms"]
                        and stamp < context["replacement"]["payload"]["host_mono_ms"], "MOCK_FRAME_RETIRED_BOUNDARY")
                require(not any(r["payload"]["state"] == "CueRequested" or r["payload"]["exposure_consumed"] for r in context["records"]),
                        "MOCK_FRAME_RETIRED_STARTED")
                retired_verified.append({"attempt_id": aid, "planning_epoch_index": index,
                    "context_clock_epoch": first["clock_epoch"], "scheduled_onset_mono_ms": first["scheduled_onset_mono_ms"],
                    "audio_request_ids": list(first["audio_request_ids"]), "loaded_mono_ms": _loaded_time(context),
                    "cancelled_mono_ms": stamp, "replacement_loaded_mono_ms": context["replacement"]["payload"]["host_mono_ms"]})
                summary_counts[aid] += 1
                incomplete.add("FRAME_CAPTURE_INCOMPLETE")
                continue
            if retired:
                require(stamp >= _loaded_time(attempts[aid]), "MOCK_FRAME_CONTEXT_BOUNDARY")
                if p["cancelled_before_window"]:
                    context = attempts[aid]
                    require(p["capture_complete"] is False and p["frame_count"] == p["within_1_5x_count"] == 0
                            and p["frame_freeze_ms"] is None and stamp < context["first"]["payload"]["scheduled_onset_mono_ms"]
                            and not any(r["payload"]["state"] == "CueRequested" or r["payload"]["exposure_consumed"] for r in context["records"]),
                            "MOCK_FRAME_CANCELLED_AFTER_CUE")
            # The monitor attributes a frame to all intersecting windows but
            # summary statistics count each physical frame index only once.
            unique = {}
            for index, gap, rate, end in grouped[aid]:
                require(end <= stamp, "MOCK_FRAME_SUMMARY_BEFORE_INTERVAL")
                require(index not in unique or unique[index] == (gap, rate), "MOCK_FRAME_DUPLICATE_CHANGED")
                unique[index] = gap, rate
            require(type(p["frame_count"]) is int and p["frame_count"] == len(unique)
                    and p["within_1_5x_count"] == sum(g <= 1500 / nominal_rate for g, _ in unique.values()), "MOCK_FRAME_COUNTS")
            require((p["frame_freeze_ms"] is None and not unique) or number(p["frame_freeze_ms"]) and unique
                    and abs(p["frame_freeze_ms"] - max(g for g, _ in unique.values())) <= 1e-6, "MOCK_FRAME_MAXIMUM")
            if p["capture_complete"] is not True or p["cancelled_before_window"] is True: incomplete.add("FRAME_CAPTURE_INCOMPLETE")
            if unique and (p["within_1_5x_count"] / len(unique) < .99 or p["frame_freeze_ms"] > 250): incomplete.add("FRAME_BUDGET_SCREEN_FAILED")
            summaries.add(aid)
            summary_counts[aid] += 1
    for aid, attempt in attempts.items():
        require(summary_counts[aid] >= len(attempt.get("retired_contexts", [])), "MOCK_FRAME_RETIRED_SUMMARY_MISSING")
    if summaries != set(attempts): incomplete.add("FRAME_ATTEMPT_COVERAGE_INCOMPLETE")
    return incomplete, {"raw_attributed_intervals": interval_count, "max_render_interval_ms": maximum,
                        "retired_unplayed_planning_epochs": retired_verified, "final_attempt_summaries": len(summaries),
                        "physical_display_timing_qualified": False}

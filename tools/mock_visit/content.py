"""Compare delivered PCM identities with pinned producer package bytes."""
from __future__ import annotations

import json
import struct

from .records import exact, hash_value, require, sha, strict


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def pcm(wav):
    require(len(wav) >= 46 and len(wav) % 2 == 0 and wav[:4] == b"RIFF" and wav[8:16] == b"WAVEfmt "
            and wav[36:40] == b"data", "MOCK_WAV_CANONICAL")
    size, fmt_size, fmt, channels, rate, byte_rate, alignment, bits, data_size = (
        struct.unpack_from("<I", wav, 4)[0], *struct.unpack_from("<IHHIIHH", wav, 16), struct.unpack_from("<I", wav, 40)[0])
    require((size, fmt_size, fmt, channels, rate, byte_rate, alignment, bits, data_size)
            == (len(wav)-8, 16, 1, 1, 48000, 96000, 2, 16, len(wav)-44), "MOCK_WAV_CANONICAL")
    return wav[44:]


def package(root, artifact, expected, read, relative):
    path = root / relative(artifact["path"])
    m = strict(read(path, artifact["sha256"]))
    require(m.get("demo") is True and m.get("study") in {"A", "B"} and m.get("package_sha256") == expected
            and sha(canonical({k:v for k,v in m.items() if k != "package_sha256"})) == expected, "MOCK_PACKAGE_IDENTITY")
    require(isinstance(m.get("files"), dict) and 3 <= len(m["files"]) <= 20000, "MOCK_PACKAGE_FILES")
    files, samples = {}, {}
    for name, entry in m["files"].items():
        exact(entry, "bytes sha256")
        raw = read(path.parent / relative(name), entry["sha256"])
        require(type(entry["bytes"]) is int and entry["bytes"] == len(raw), "MOCK_PACKAGE_SIZE")
        files[name] = raw
        if name.endswith(".wav"): samples[name] = pcm(raw)
    require("audio.json" in files, "MOCK_PACKAGE_AUDIO_INDEX")
    audio = strict(files["audio.json"])
    require(audio["study"] == m["study"], "MOCK_PACKAGE_AUDIO_SCOPE")
    atoms = {}
    for row in audio.get("atoms", audio.get("options", [])):
        key = row["atom_id"] if m["study"] == "A" else (row["profile"], row["atom_id"], row["rank"])
        require(key not in atoms and row["path"] in samples and sha(samples[row["path"]]) == row["pcm_sha256"]
                and sha(files[row["path"]]) == row["file_sha256"] and len(samples[row["path"]]) // 2 == row["n_samples"], "MOCK_PACKAGE_ATOM")
        atoms[key] = row
    require(len(atoms) == (16 if m["study"] == "A" else 192), "MOCK_PACKAGE_ATOM_COUNT")
    messages = {}
    for row in audio["messages"]:
        require(row["message_id"] not in messages, "MOCK_PACKAGE_MESSAGE_DUPLICATE")
        messages[row["message_id"]] = row
        combos = [row] if m["study"] == "A" else row["combinations"]
        for c in combos:
            keys = (row["action_atom"], row["referent_atom"]) if m["study"] == "A" else ((c["profile"], row["action_atom"], c["action_rank"]), (c["profile"], row["referent_atom"], c["referent_rank"]))
            a, r = [samples[atoms[k]["path"]] for k in keys]
            composite = a + bytes(9600*2) + r
            require(sha(composite) == c["composite_sha256"] and len(composite)//2 == c["n_samples"], "MOCK_PACKAGE_COMPOSITION")
            if m["study"] == "A" and row["path"] is not None:
                require(row["status"] == "trained" and samples[row["path"]] == composite and sha(files[row["path"]]) == row["file_sha256"], "MOCK_PACKAGE_TRAINED_FILE")
        if row["status"] == "heldout": require(m["study"] == "B" or row["path"] is row["file_sha256"] is None, "MOCK_PACKAGE_HELDOUT_FILE")
    require(len(messages) == 32, "MOCK_PACKAGE_MESSAGE_COUNT")
    return m, atoms, messages


def profile_examples(config, config_root, schedule, role, read, relative):
    """Resolve the exact stored profile order and reserved canonical examples."""
    rows={}
    for name in ("reserved_registry","menu_allocation"):
        pin=config["files"].get(name)
        if pin is None: return None,None
        exact(pin,"path sha256");rows[name]=strict(read(config_root/relative(pin["path"]),pin["sha256"]))
    registry,allocation=rows["reserved_registry"],rows["menu_allocation"]
    require(type(registry.get("registry_version")) is int and registry["registry_version"] == 1
            and isinstance(registry.get("entries"),list) and allocation.get("demo") is True, "MOCK_PROFILE_REGISTRY")
    dyads=[d for d in allocation["dyads"] if any(m["slot_id"] == schedule["person_slot"] for m in d["members"])]
    require(len(dyads) == 1,"MOCK_PROFILE_ALLOCATION")
    members=[m for m in dyads[0]["members"] if m["slot_id"] == schedule["person_slot"]]
    order=dyads[0]["profile_menu_order"]
    require(len(members) == 1 and members[0]["role"] == role and isinstance(order,list)
            and sorted(order) == ["P1","P2","P3"], "MOCK_PROFILE_ALLOCATION")
    directory=config["directories"].get("menu_examples")
    if directory is None: return None,None
    examples={}
    for profile in order:
        matched=[r for r in registry["entries"] if r.get("id") == "calibration-"+profile]
        require(len(matched) == 1,"MOCK_PROFILE_EXAMPLE")
        row=matched[0]
        require(row.get("kind") == "calibration" and row.get("profile") == profile and row.get("recipe") is None
                and type(row.get("n_samples")) is int and row["n_samples"] == 96000, "MOCK_PROFILE_EXAMPLE")
        wave=read(config_root/relative(directory)/("calibration-"+profile+".wav"),row["file_sha256"])
        require(len(pcm(wave)) == 192000 and sha(pcm(wave)) == row["pcm_sha256"],"MOCK_PROFILE_PCM")
        examples[profile]=row
    return order,examples


def grammar(config, config_root, rows, read, relative):
    pin=config["files"].get("reserved_registry");directory=config["directories"].get("grammar")
    if pin is None or directory is None:return {"GRAMMAR_REGISTRY_BINDING_MISSING"},0
    exact(pin,"path sha256");registry=strict(read(config_root/relative(pin["path"]),pin["sha256"]))
    require(type(registry.get("registry_version")) is int and registry["registry_version"] == 1,"MOCK_GRAMMAR_REGISTRY")
    options={}
    for phase,id,kind,count in (("ready","ready-cue","ready_cue",15360),("clicks","click-grammar-demo","click",13824)):
        matches=[x for x in registry["entries"] if x.get("id") == id]
        require(len(matches) == 1,"MOCK_GRAMMAR_REGISTRY")
        option=matches[0]
        require(option["kind"] == kind and type(option["n_samples"]) is int and option["n_samples"] == count,"MOCK_GRAMMAR_REGISTRY")
        wave=read(config_root/relative(directory)/(id+".wav"),option["file_sha256"])
        require(len(pcm(wave)) == count*2 and sha(pcm(wave)) == option["pcm_sha256"],"MOCK_GRAMMAR_PCM")
        options[phase]=option
    checked=set()
    for row in rows:
        p=row["payload"]
        require(p["registry_sha256"] == pin["sha256"],"MOCK_GRAMMAR_REGISTRY_PIN")
        if p["kind"] != "audio":continue
        a=p["audio"];option=options[p["phase"]]
        require(a["pcm_sha256"] == option["pcm_sha256"] and a["waveform_sha256"] == option["file_sha256"],"MOCK_GRAMMAR_DELIVERED_HASH")
        if a["code"] == "AUDIO_PLAYBACK_COMPLETED":
            require(a["delivered_samples"] == option["n_samples"] and a["callback_count"] > 0,"MOCK_TRUNCATED_COMPLETION")
            checked.add(p["audio_request_id"])
    return set(),len(checked)


def verify(root, artifacts, items, requests, package_hash, read, relative, observations=None,
           *, config=None, config_root=None, schedule=None, joined=(), menu_chains=(), role=None):
    missing, checked = set(), 0
    if len(artifacts["package_manifest"]) != 1: return {"CONTENT_PACKAGE_EVIDENCE_MISSING"}, 0, None
    m, atoms, messages = package(root, artifacts["package_manifest"][0][0], package_hash, read, relative)
    selected = {};snap=None;order=None;examples=None
    speech = {}
    if artifacts["speech_manifest"]:
        require(len(artifacts["speech_manifest"]) == 1, "MOCK_SPEECH_MANIFEST_COUNT")
        entry, raw = artifacts["speech_manifest"][0]
        path = root / relative(entry["path"]); value = strict(raw)
        require(value.get("demo") is True and value.get("status") == "engineering_unreviewed"
                and value.get("listening_review_sha256") is None and value.get("study") == m["study"], "MOCK_SPEECH_SIMULATION_SCOPE")
        require(isinstance(value.get("items"),list) and len(value["items"])==64, "MOCK_SPEECH_ITEMS")
        read(path.parent/"selection.local.json",value["source"]["speech_list_sha256"])
        for entry in value["items"]:
            require(isinstance(entry.get("speech_id"),str) and entry["speech_id"] not in speech, "MOCK_SPEECH_ID")
            wave=read(path.parent/relative(entry["speech_id"]+".wav"),entry["sha256"])
            require(sha(pcm(wave))==entry["pcm_sha256"] and len(wave[44:])//2==entry["samples"], "MOCK_SPEECH_PCM")
            speech[entry["speech_id"]]=entry
        from tools.validity_speech import validate_manifest
        try: validate_manifest(value,path.parent)
        except ValueError: require(False,"MOCK_SPEECH_PRODUCER_VALIDATION")
    if m["study"] == "B":
        from . import store
        if config is None: return {"STORE_INITIAL_BINDING_MISSING"},0,None
        store_missing,snap=store.verify(config,config_root,joined,menu_chains,atoms,package_hash,read,relative,role,schedule["visit"])
        missing.update(store_missing)
        if snap is None: return missing,0,None
        require(len(artifacts["selection_snapshot"]) <= 1,"MOCK_SELECTION_SNAPSHOT_COUNT")
        if artifacts["selection_snapshot"]:
            require(strict(artifacts["selection_snapshot"][0][1]) == snap,"MOCK_SELECTION_SNAPSHOT_DISAGREES_WITH_STORE")
        for row in snap["entries"]:
            require(row["atom_id"] not in selected and type(row["rank"]) is int and 1 <= row["rank"] <= 3, "MOCK_SELECTION_ENTRY")
            key = row["profile"], row["atom_id"], row["rank"]
            require(key in atoms and row["pcm_sha256"] == atoms[key]["pcm_sha256"] and row["file_sha256"] == atoms[key]["file_sha256"], "MOCK_SELECTION_HASH")
            selected[row["atom_id"]] = key
        if any(i["block"] == "profile_menu" for i in items.values()):
            order,examples=profile_examples(config,config_root,schedule,role,read,relative)
            if examples is None: missing.add("PROFILE_EXAMPLE_BINDING_REQUIRED")
        for chain in menu_chains:
            for row in chain:
                p=row["record"]
                if p.get("kind") == "selection_verified" and p["menu_key"] == "profile" and order is not None:
                    require(type(p["selected_index"]) is int and 1 <= p["selected_index"] <= 3
                            and order[p["selected_index"]-1] == snap["profile"],"MOCK_PROFILE_SELECTION_ORDER")
    positions={}
    for request in requests.values():
        item, p = items[request["opportunity_id"]], request["payload"]
        positions[request["opportunity_id"]]=positions.get(request["opportunity_id"],0)+1
        index=positions[request["opportunity_id"]]-1
        expected_samples=None
        if item["block"] == "profile_menu":
            if examples is None: continue
            require(index < 8 and snap["profile"] in examples,"MOCK_PROFILE_PLAY_ORDER")
            selected_profile=(order[0],order[0],order[1],order[1],order[2],order[2],snap["profile"],snap["profile"])[index]
            example=examples[selected_profile]
            require(p["pcm_sha256"] == example["pcm_sha256"] and p["waveform_sha256"] == example["file_sha256"],"MOCK_PROFILE_PLAY_ORDER")
            if observations is not None:
                for obs in observations.get(request["audio_request_id"],[]):
                    if obs["payload"]["code"] == "AUDIO_PLAYBACK_COMPLETED":
                        require(obs["payload"]["delivered_samples"] == 96000 and obs["payload"]["callback_count"] > 0,"MOCK_TRUNCATED_COMPLETION")
            checked+=1;continue
        if item["trial_type"] == "speech":
            if not speech: missing.add("SPEECH_SELECTION_BINDING_REQUIRED"); continue
            parts=item["speech_id"].split("-")
            require(len(parts)==3,"MOCK_SPEECH_SCHEDULE_ID")
            sid=f"speech-{parts[1].lower()}-{parts[2].lower()}-t1"
            require(sid in speech and speech[sid]["chosen"] is True and p["pcm_sha256"]==speech[sid]["pcm_sha256"]
                    and p["waveform_sha256"]==speech[sid]["sha256"], "MOCK_DELIVERED_SPEECH_HASH")
            expected_samples=speech[sid]["samples"]
            if observations is not None:
                for obs in observations.get(request["audio_request_id"],[]):
                    if obs["payload"]["code"]=="AUDIO_PLAYBACK_COMPLETED":require(obs["payload"]["delivered_samples"]==expected_samples,"MOCK_TRUNCATED_COMPLETION")
            checked+=1;continue
        atom_id, message_id = item.get("atom_id"), item.get("message_id")
        if atom_id:
            if item["block"] == "atom_menus":
                require(atom_id in selected and index < 8,"MOCK_MENU_SELECTION_MISSING")
                choice=selected[atom_id];rank=(1,1,2,2,3,3,choice[2],choice[2])[index]
                candidate=atoms[choice[0],atom_id,rank]
                require(candidate["pcm_sha256"] == p["pcm_sha256"] and candidate["file_sha256"] == p["waveform_sha256"],"MOCK_MENU_CANDIDATE_HASH")
                expected_samples=candidate["n_samples"]
            else:
                key = atom_id if m["study"] == "A" else selected.get(atom_id)
                require(key in atoms, "MOCK_SELECTED_ATOM_MISSING")
                a = atoms[key]
                require(p["pcm_sha256"] == a["pcm_sha256"] and p["waveform_sha256"] == a["file_sha256"], "MOCK_DELIVERED_ATOM_HASH")
                expected_samples=a["n_samples"]
        elif message_id:
            require(message_id in messages, "MOCK_MESSAGE_ID")
            msg = messages[message_id]
            require((msg["status"] == "heldout") == (item["block"] == "novel"), "MOCK_HELDOUT_OUTSIDE_NOVEL")
            a_key, r_key = (msg["action_atom"], msg["referent_atom"]) if m["study"] == "A" else (selected.get(msg["action_atom"]), selected.get(msg["referent_atom"]))
            require(a_key in atoms and r_key in atoms, "MOCK_MESSAGE_SELECTION_MISSING")
            if m["study"] == "A": c = msg
            else:
                matching = [c for c in msg["combinations"] if c["profile"] == a_key[0] == r_key[0] and c["action_rank"] == a_key[2] and c["referent_rank"] == r_key[2]]
                require(len(matching) == 1, "MOCK_MESSAGE_SELECTION_MISSING"); c = matching[0]
            require(p["pcm_sha256"] == c["composite_sha256"], "MOCK_DELIVERED_MESSAGE_HASH")
            expected_samples=c["n_samples"]
            if m["study"] == "A" and msg["status"] == "trained": require(p["waveform_sha256"] == msg["file_sha256"], "MOCK_TRAINED_FILE_HASH")
            else:
                require(p["waveform_sha256"] is None and p["action_pcm_sha256"] == atoms[a_key]["pcm_sha256"]
                        and p["referent_pcm_sha256"] == atoms[r_key]["pcm_sha256"], "MOCK_COMPOSITE_PROVENANCE")
        else: require(False, "MOCK_AUDIO_CONTENT_ID_MISSING")
        if observations is not None and expected_samples is not None:
            for obs in observations.get(request["audio_request_id"],[]):
                if obs["payload"]["code"]=="AUDIO_PLAYBACK_COMPLETED":require(obs["payload"]["delivered_samples"]==expected_samples,"MOCK_TRUNCATED_COMPLETION")
        checked += 1
    return missing, checked, snap

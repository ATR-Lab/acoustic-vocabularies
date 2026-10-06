"""Reconcile real file-mailbox checkpoints without importing a sound runtime."""
from .content import canonical
from .records import exact, guid, hash_value, number, require, sha, strict

SNAPSHOT = "schema_version unit_id book_id bank_sha256 package_sha256 config_sha256 profile profile_selection_receipt_sha256 book_head snapshot_sha256 journal_head source_kind participant_ready entries manifest_sha256"
REQUEST = "schema_version request_id operation unit_id book_id bank_sha256 package_sha256 expected_head expected_snapshot_sha256 profile menu_key rank"
RECEIPT = "schema_version request_id operation unit_id book_id bank_sha256 package_sha256 profile menu_key rank request_sha256 config_sha256 status accepted reason before_head after_head before_snapshot_sha256 after_snapshot_sha256 pcm_sha256 file_sha256 source_kind participant_ready receipt_sha256"


def self_hash(value, key):
    require(hash_value(value[key]) and sha(canonical({k:v for k,v in value.items() if k != key})) == value[key], "MOCK_STORE_SELF_HASH")


def snapshot(value, binding, atoms):
    exact(value, SNAPSHOT)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["source_kind"] == "synthetic" and value["participant_ready"] is False,
            "MOCK_STORE_SCOPE")
    require(all(value[k] == binding[k] for k in binding), "MOCK_STORE_BINDING")
    self_hash(value, "manifest_sha256")
    require(isinstance(value["entries"], list) and len(value["entries"]) <= 16, "MOCK_STORE_ENTRIES")
    entries = {}
    for row in value["entries"]:
        exact(row, "atom_id profile rank pcm_sha256 file_sha256 selection_receipt_sha256")
        key = row["profile"], row["atom_id"], row["rank"]
        require(type(row["rank"]) is int and 1 <= row["rank"] <= 3 and row["atom_id"] not in entries
                and row["profile"] == value["profile"] and key in atoms
                and all(row[k] == atoms[key][k] for k in ("pcm_sha256", "file_sha256"))
                and hash_value(row["selection_receipt_sha256"]), "MOCK_STORE_ENTRY")
        entries[row["atom_id"]] = row
    # The upstream full snapshot also commits recipe/semantic fields that this
    # deliberately smaller mailbox projection never exposes. Its digest is an
    # opaque retained commitment, linked through every request and receipt; do
    # not pretend to recompute it from only PCM hashes.
    require(hash_value(value["snapshot_sha256"]),"MOCK_STORE_SNAPSHOT_HASH")
    if value["profile"] is None:
        require(not entries and value["snapshot_sha256"] == sha(b"{}")
                and all(value[k] is None for k in ("book_head", "journal_head", "profile_selection_receipt_sha256")), "MOCK_STORE_EMPTY")
    else:
        require(value["profile"] in {"P1","P2","P3"} and all(hash_value(value[k]) for k in ("book_head", "journal_head", "profile_selection_receipt_sha256")), "MOCK_STORE_PROFILE")
    return entries


def unchanged(before, after):
    require(before["profile"] is None or before["profile"] == after["profile"], "MOCK_STORE_PROFILE_CHANGED")
    old = {x["atom_id"]:x for x in before["entries"]}; new = {x["atom_id"]:x for x in after["entries"]}
    require(all(k in new and new[k] == row for k,row in old.items()), "MOCK_STORE_OLD_ENTRY_CHANGED")
    if before["profile"] is not None:
        require(before["profile_selection_receipt_sha256"] == after["profile_selection_receipt_sha256"], "MOCK_STORE_PROFILE_CHANGED")


def verify(config, config_root, joined, menu_chains, atoms, package_hash, read, relative, role, visit):
    missing = set()
    def load(name):
        pin = config["files"].get(name)
        if pin is None: return None
        exact(pin,"path sha256")
        return strict(read(config_root / relative(pin["path"]), pin["sha256"]))
    initial, bridge = load("menu_snapshot"), load("menu_bridge_config")
    if initial is None or bridge is None: return {"STORE_INITIAL_BINDING_MISSING"}, None
    exact(bridge,"schema_version demo_only unit_id book_id bank_path bank_file_sha256 bank_sha256 package_path package_sha256 store_root")
    require(type(bridge["schema_version"]) is int and bridge["schema_version"] == 1 and bridge["demo_only"] is True
            and bridge["unit_id"] == "DEMO-" + config["identity"]["unit_id"]
            and bridge["package_sha256"] == package_hash and bridge["bank_sha256"] == config["pins"]["bank_sha256"], "MOCK_STORE_CONFIG")
    binding = {k:bridge[k] for k in ("unit_id","book_id","bank_sha256","package_sha256")}
    binding["config_sha256"] = sha(canonical(bridge))
    snapshot(initial, binding, atoms)
    require(initial["manifest_sha256"] == config["pins"]["menu_manifest_sha256"]
            and initial["book_head"] == config["pins"]["menu_head_sha256"]
            and initial["snapshot_sha256"] == config["pins"]["menu_snapshot_sha256"], "MOCK_STORE_INITIAL_PIN")
    current, pending, seen, last_verify = initial, None, set(), None
    verification_times=[]
    for row in joined:
        if row["kind"] != "store": continue
        p = row["payload"]
        require(number(p.get("mono_ms")), "MOCK_STORE_TIME")
        if p.get("event") == "menu_store_intent":
            exact(p,"event mono_ms request"); q=p["request"]; exact(q, REQUEST)
            require(pending is None and guid(q["request_id"]) and q["request_id"] not in seen, "MOCK_STORE_REQUEST_ORDER")
            require(type(q["schema_version"]) is int and q["schema_version"] == 1
                    and all(q[k] == binding[k] for k in ("unit_id","book_id","bank_sha256","package_sha256"))
                    and q["expected_head"] == current["book_head"] and q["expected_snapshot_sha256"] == current["snapshot_sha256"], "MOCK_STORE_REQUEST_BINDING")
            require(q["operation"] in {"verify","profile","atom"}, "MOCK_STORE_OPERATION")
            if q["operation"] == "verify": require(q["profile"] == current["profile"] and q["menu_key"] == "verify" and q["rank"] is None, "MOCK_STORE_VERIFY_REQUEST")
            else:
                require(role == "active" and visit in {"V1","V2","V3"} and q["profile"] in {"P1","P2","P3"}, "MOCK_STORE_READONLY_WRITE")
                if q["operation"] == "profile": require(visit == "V1" and q["menu_key"] == "profile" and q["rank"] is None and current["profile"] is None, "MOCK_STORE_PROFILE_REQUEST")
                else: require(type(q["rank"]) is int and 1 <= q["rank"] <= 3 and (q["profile"],q["menu_key"],q["rank"]) in atoms, "MOCK_STORE_ATOM_REQUEST")
            pending=(q,p["mono_ms"]); seen.add(q["request_id"])
        elif p.get("event") == "menu_store_verified":
            exact(p,"event mono_ms response"); require(pending is not None, "MOCK_STORE_RESPONSE_WITHOUT_REQUEST")
            q,stamp=pending; r=p["response"]
            exact(r,"schema_version request_id request_sha256 config_sha256 receipt snapshot error response_sha256")
            self_hash(r,"response_sha256")
            require(type(r["schema_version"]) is int and r["schema_version"] == 1 and r["request_id"] == q["request_id"]
                    and r["request_sha256"] == sha(canonical(q)) and r["config_sha256"] == binding["config_sha256"]
                    and r["error"] is None and p["mono_ms"] >= stamp, "MOCK_STORE_RESPONSE_BINDING")
            after=r["snapshot"]; snapshot(after,binding,atoms)
            if q["operation"] == "verify":
                require(r["receipt"] is None and after == current, "MOCK_STORE_VERIFY_CHANGED_STATE")
                last_verify=p["mono_ms"]
                verification_times.append(last_verify)
            else:
                receipt=r["receipt"]; exact(receipt,RECEIPT);self_hash(receipt,"receipt_sha256")
                require(all(receipt[k] == q[k] for k in ("schema_version","request_id","operation","unit_id","book_id","bank_sha256","package_sha256","profile","menu_key","rank"))
                        and receipt["request_sha256"] == r["request_sha256"] and receipt["config_sha256"] == binding["config_sha256"]
                        and receipt["before_head"] == current["book_head"] and receipt["before_snapshot_sha256"] == current["snapshot_sha256"]
                        and receipt["after_head"] == after["book_head"] and receipt["after_snapshot_sha256"] == after["snapshot_sha256"]
                        and receipt["source_kind"] == "synthetic" and receipt["participant_ready"] is False and type(receipt["accepted"]) is bool, "MOCK_STORE_RECEIPT_BINDING")
                unchanged(current,after)
                if not receipt["accepted"]:
                    require(q["operation"] == "atom" and receipt["status"] == "rejected" and receipt["reason"] == "E_REJECTED"
                            and after["entries"] == current["entries"] and after["book_head"] == current["book_head"]
                            and receipt["pcm_sha256"] is receipt["file_sha256"] is None, "MOCK_STORE_REJECTION")
                    missing.add("STORE_SELECTION_REJECTED")
                else:
                    require(receipt["reason"] is None and after["profile"] == q["profile"], "MOCK_STORE_ACCEPTED_RECEIPT")
                    if q["operation"] == "profile":
                        require(receipt["status"] == "profile_selected" and not after["entries"]
                                and after["profile_selection_receipt_sha256"] == receipt["receipt_sha256"]
                                and receipt["pcm_sha256"] is receipt["file_sha256"] is None, "MOCK_STORE_PROFILE_RECEIPT")
                    else:
                        old={x["atom_id"] for x in current["entries"]}; new={x["atom_id"]:x for x in after["entries"]}
                        require(receipt["status"] == "committed" and q["menu_key"] not in old and set(new) == old | {q["menu_key"]}, "MOCK_STORE_ATOM_GROWTH")
                        added=new[q["menu_key"]]
                        require(added["rank"] == q["rank"] and all(added[k] == receipt[k] for k in ("pcm_sha256","file_sha256"))
                                and added["selection_receipt_sha256"] == receipt["receipt_sha256"], "MOCK_STORE_ATOM_RECEIPT")
                last_verify=None
            current=after;pending=None
        else: require(False,"MOCK_STORE_EVENT")
    if pending is not None: missing.add("STORE_PENDING_REQUEST")
    expected={"V1":8,"V2":12,"V3":16,"W1":16,"W4":16}[visit]
    if len(current["entries"]) != expected: missing.add("STORE_FINAL_WAVE_INCOMPLETE")
    selections=[r["record"] for c in menu_chains for r in c if r["record"].get("kind") == "selection_verified"]
    for p in selections:
        if p["menu_key"] == "profile":
            require(p["receipt_sha256"] == current["profile_selection_receipt_sha256"], "MOCK_MENU_PROFILE_RECEIPT_LINK")
        else:
            selected=[x for x in current["entries"] if x["atom_id"] == p["menu_key"]]
            require(len(selected) == 1 and selected[0]["rank"] == p["selected_index"]
                    and selected[0]["selection_receipt_sha256"] == p["receipt_sha256"], "MOCK_MENU_SELECTION_RECEIPT_LINK")
    boundary=max((p["mono_ms"] for p in selections),default=0)
    if last_verify is None or last_verify < boundary: missing.add("STORE_FINAL_FRESH_VERIFICATION_MISSING")
    post_menu=[r["host_mono_ms"] for r in joined if r["kind"] == "module" and r["payload"].get("kind") == "prepare"
               and r["payload"].get("block") not in {"profile_menu","atom_menus"} and r["host_mono_ms"] >= boundary]
    if selections and post_menu and not any(boundary <= t <= min(post_menu) for t in verification_times):
        missing.add("STORE_VERIFICATION_BEFORE_HANDOFF_MISSING")
    return missing,current


def compare_growth(snapshots):
    """Exact visit-owned receipts/old entries survive each 8/12/16 transition."""
    missing=[];compared=0
    for role in ("active","yoked"):
        previous=None
        for visit,count in (("V1",8),("V2",12),("V3",16),("W1",16),("W4",16)):
            current=snapshots.get((visit,role))
            if current is None: missing.append(role+"/"+visit);continue
            require(len(current["entries"]) == count,"MOCK_CROSS_VISIT_GROWTH_COUNT")
            if previous is not None:
                require(all(previous[k] == current[k] for k in ("unit_id","book_id","bank_sha256","package_sha256","profile")), "MOCK_CROSS_VISIT_STORE_BINDING")
                unchanged(previous,current);compared+=1
                if visit in {"W1","W4"}:
                    require(all(previous[k] == current[k] for k in ("book_head","snapshot_sha256","journal_head")),"MOCK_READONLY_VISIT_STORE_CHANGED")
            previous=current
    for visit in ("V1","V2","V3","W1","W4"):
        if (visit,"active") in snapshots and (visit,"yoked") in snapshots:
            a,y=snapshots[visit,"active"],snapshots[visit,"yoked"]
            require(all(a[k] == y[k] for k in ("unit_id","book_id","package_sha256","bank_sha256","profile","profile_selection_receipt_sha256","entries")), "MOCK_YOKED_STORE_SELECTION_CHANGED")
    return {"cross_visit_growth_verified": not missing and compared == 8,"transitions_checked":compared,"missing_visit_snapshots":missing}

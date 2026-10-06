/*
 * Rater station page (#21). Plain JavaScript, no third-party code.
 *
 * Protocol: av_generation.rater_protocol (rater-message.schema.json, version 1).
 * Rules (generation/docs/rater-panel.md):
 * - one click at the start unlocks audio; there is no replay, seek or volume control;
 * - every asset is fetched and SHA-256-checked before its slot (asset_ready);
 * - the clock offset to the server comes from bursts of chained sync probes; audio is
 *   scheduled on the audio clock at the server-scheduled onsets;
 * - each slot is handled at most once per page session (sessionStorage), and each
 *   (slot, role) is played at most once; a slot joined after its candidate onset is shown
 *   as a neutral screen with no audio and no rating controls;
 * - a slot whose candidate (or, when distinguishability is asked, reference) did not
 *   start (asset failed, too late, audio not running) is shown as a neutral screen with
 *   no controls: a rater who did not hear the sound cannot rate it;
 * - controls unlock at unlock_offset_ms and lock at 20 s; ratings go to the server only;
 * - a withdrawal is kept until the server answers it (the page reconnects for it); an
 *   `end` broadcast to every station never shows the withdrawal screen;
 * - a socket closed with REPLACED_CLOSE_CODE (another page took this seat) is not
 *   reconnected.
 */
(() => {
  "use strict";

  const PROTOCOL_VERSION = 1;
  const STATION_KIND = "human";
  const WS_PATH = "/panel/ws";
  const ASSET_URL_RE = /^\/panel\/assets\/[0-9a-f]{64}\.wav$/;
  const STATION_RE = /^S[0-9]{1,2}$/;
  const RATER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;
  const KEY_RE = /^[0-9a-f]{32}$/;
  const REPLACED_CLOSE_CODE = 4001;
  const SLOT_MS = 20000;
  const REFERENCE_OFFSET_MS = 2000;
  const ONSET_TOLERANCE_MS = 50;
  const MAX_LATE_START_MS = 1000;
  const REPORT_DELAY_MS = 100;
  const SYNC_BURST = 8;
  const SYNC_INTERVAL_MS = 60000;
  const RECONNECT_DELAYS_MS = [250, 500, 1000, 2000, 4000];
  const STORAGE_PREFIX = "av-panel-station";

  // ---------------------------------------------------------------------
  // SHA-256 (FIPS 180-4). crypto.subtle exists only in secure contexts (https or
  // localhost); lab stations reach the server over plain http on the LAN.

  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  function sha256HexJs(input) {
    const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
    const n = bytes.length;
    const padded = new Uint8Array(((n + 9 + 63) >> 6) << 6);
    padded.set(bytes);
    padded[n] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(n / 0x20000000), false);
    view.setUint32(padded.length - 4, (n << 3) >>> 0, false);
    const h = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    const w = new Uint32Array(64);
    const rotr = (x, k) => (x >>> k) | (x << (32 - k));
    for (let off = 0; off < padded.length; off += 64) {
      for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + 4 * i, false);
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = rotr(a, 7) ^ rotr(a, 18) ^ (a >>> 3);
        const s1 = rotr(b, 17) ^ rotr(b, 19) ^ (b >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) >>> 0;
        const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
        hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    return Array.from(h, (x) => x.toString(16).padStart(8, "0")).join("");
  }

  async function sha256Hex(buffer) {
    if (window.crypto && window.crypto.subtle && window.isSecureContext) {
      const digest = await window.crypto.subtle.digest("SHA-256", buffer);
      return Array.from(new Uint8Array(digest), (x) => x.toString(16).padStart(2, "0")).join("");
    }
    return sha256HexJs(new Uint8Array(buffer));
  }

  // ---------------------------------------------------------------------
  // State

  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(window.location.search);
  const st = {
    station: params.get("station") || "",
    rater: params.get("rater") || "",
    key: KEY_RE.test(params.get("key") || "") ? params.get("key") : null,
    ctx: null,
    ws: null,
    welcomed: false,
    sessionId: null,
    finished: false,
    attempt: 0,
    offset: null,          // server_ms - performance.now()
    rtt: null,
    syncSeq: 0,
    burst: null,
    lastSync: -Infinity,
    syncWaiters: [],
    buffers: new Map(),    // asset_id -> AudioBuffer
    loading: new Map(),    // asset_id -> Promise<AudioBuffer|null>
    seen: new Set(),       // rating_slot_ids handled by this page session
    played: new Set(),     // "rating_slot_id|role"
    slots: new Map(),      // live slots by id
    active: null,
    formSlot: null,        // the slot whose controls the form shows
    pause: null,
    withdrawal: null,      // {reason, sentOn}: kept until the server answers it
    outbox: [],
    log: [],
  };

  const now = () => performance.now();

  function record(event, detail) {
    st.log.push({ t: Math.round(now()), event, detail: detail === undefined ? null : detail });
    if (st.log.length > 2000) st.log.splice(0, st.log.length - 2000);
  }

  // ---------------------------------------------------------------------
  // Persistence (no replay after a page reload)

  function storageKey() {
    return `${STORAGE_PREFIX}:${st.sessionId}:${st.station}`;
  }

  function loadPersisted() {
    try {
      const raw = window.sessionStorage.getItem(storageKey());
      if (!raw) return;
      const data = JSON.parse(raw);
      for (const id of data.seen || []) st.seen.add(id);
      for (const key of data.played || []) st.played.add(key);
    } catch (err) {
      record("storage_error", String(err));
    }
  }

  function persist() {
    if (!st.sessionId) return;
    try {
      window.sessionStorage.setItem(
        storageKey(),
        JSON.stringify({ seen: Array.from(st.seen), played: Array.from(st.played) }),
      );
    } catch (err) {
      record("storage_error", String(err));
    }
  }

  // ---------------------------------------------------------------------
  // Screens

  const SCREENS = [
    "start", "wait", "slot", "placeholder", "rejoin", "missed", "pause", "end", "withdrawn", "error",
  ];

  function show(name) {
    for (const s of SCREENS) $(`screen-${s}`).hidden = s !== name;
    $("withdraw-button").hidden = st.finished || name === "start";
  }

  function setConnection(online) {
    const el = $("connection");
    el.dataset.state = online ? "online" : "offline";
    el.textContent = online ? "Connected" : st.ctx ? "Connection lost. Reconnecting..." : "Not connected";
  }

  function progress(el, slot) {
    const done = Math.min(1, Math.max(0, (now() - slot.startPerf) / SLOT_MS));
    el.style.transition = "none";
    el.style.width = `${done * 100}%`;
    void el.offsetWidth;
    el.style.transition = `width ${Math.max(0, slot.lockPerf - now())}ms linear`;
    el.style.width = "100%";
  }

  // ---------------------------------------------------------------------
  // Connection

  function send(message) {
    if (!st.ws || st.ws.readyState !== WebSocket.OPEN || !st.welcomed) return false;
    st.ws.send(JSON.stringify(message));
    return true;
  }

  function queue(message) {
    if (!send(message)) st.outbox.push(message);
  }

  function withKey(path) {
    return st.key ? `${path}?key=${st.key}` : path;
  }

  function closeSocket() {
    if (st.ws) st.ws.close(1000);
  }

  function connect() {
    if (st.finished && !st.withdrawal) return;
    const scheme = window.location.protocol === "https:" ? "wss://" : "ws://";
    const ws = new WebSocket(scheme + window.location.host + withKey(WS_PATH));
    st.ws = ws;
    st.welcomed = false;
    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: "hello",
        protocol_version: PROTOCOL_VERSION,
        station: st.station,
        rater_id: st.rater,
        kind: STATION_KIND,
        client_ms: Math.round(now() * 1000) / 1000,
        resume_rating_slot_id: st.active ? st.active.id : null,
      }));
    };
    ws.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (err) {
        record("bad_frame", String(err));
        return;
      }
      if (ws === st.ws) handle(message);
    };
    ws.onclose = (event) => {
      if (ws !== st.ws) return;
      st.ws = null;
      st.welcomed = false;
      st.burst = null;
      setConnection(false);
      record("disconnected", event.code);
      if (event.code === REPLACED_CLOSE_CODE) {
        // Another page or device joined as this station: stop here, never take the
        // seat back (two pages would otherwise replace each other forever).
        st.withdrawal = null;
        finish("error");
        $("error-text").textContent =
          "This station was opened in another window. Please tell the session operator.";
        return;
      }
      if (st.finished && !st.withdrawal) return;
      const delay = RECONNECT_DELAYS_MS[Math.min(st.attempt, RECONNECT_DELAYS_MS.length - 1)];
      st.attempt += 1;
      window.setTimeout(connect, delay);
    };
  }

  function handle(message) {
    if (st.withdrawal) {
      whileWithdrawing(message);
      return;
    }
    switch (message.type) {
      case "welcome":
        st.welcomed = true;
        st.attempt = 0;
        st.sessionId = message.session_id;
        setConnection(true);
        record("welcome", message.state);
        loadPersisted();
        startBurst();
        for (const queued of st.outbox.splice(0)) send(queued);
        applyState(message.state);
        break;
      case "sync_reply":
        onSyncReply(message);
        break;
      case "preload":
        for (const asset of message.assets) ensureBuffer(asset.asset_id, asset.url, asset.n_bytes);
        break;
      case "slot":
        whenSynced(() => onSlot(message));
        break;
      case "rating_ack":
        onAck(message);
        break;
      case "pause":
        onPause(message.reason);
        break;
      case "resume":
        onResume();
        break;
      case "end":
        // `end withdrawn` before `welcome` is the server's answer to a hello from a
        // withdrawn seat; after `welcome` it is a broadcast (another rater withdrew and
        // the session ended), which is the normal end for this station.
        finish(message.reason === "withdrawn" && !st.welcomed ? "withdrawn" : "end");
        break;
      case "error":
        record("error", `${message.code} ${message.message}`);
        if (message.code === "E_UNKNOWN_RATER") {
          finish("error");
          $("error-text").textContent = "This station is not part of the session. Please tell the session operator.";
        }
        break;
      default:
        record("unknown_message", String(message.type));
    }
  }

  function applyState(state) {
    if (state === "ended") {
      finish("end");
    } else if (state === "paused" || state === "between_atoms") {
      st.pause = state;
      if (!st.active) show("pause");
    } else if (!st.active && !st.pause) {
      show("wait");
    }
  }

  // ---------------------------------------------------------------------
  // Clock sync: probe i + 1 is sent when reply i arrives (its client_ms is T4 of i)

  function startBurst() {
    st.burst = { index: 0, samples: [], sent: 0 };
    probe();
  }

  function probe() {
    st.burst.sent = now();
    send({ type: "sync_request", seq: st.syncSeq + st.burst.index, client_ms: Math.round(st.burst.sent * 1000) / 1000 });
  }

  function onSyncReply(message) {
    const burst = st.burst;
    if (!burst || message.seq !== st.syncSeq + burst.index) return;
    const received = now();
    burst.samples.push({ rtt: received - burst.sent, offset: message.server_ms - (burst.sent + received) / 2 });
    burst.index += 1;
    if (burst.index < SYNC_BURST) {
      probe();
      return;
    }
    burst.samples.sort((x, y) => x.rtt - y.rtt);
    st.offset = burst.samples[0].offset;
    st.rtt = burst.samples[0].rtt;
    st.syncSeq += SYNC_BURST;
    st.burst = null;
    st.lastSync = received;
    record("clock_sync", { offset: st.offset, rtt: st.rtt });
    for (const fn of st.syncWaiters.splice(0)) fn();
  }

  function whenSynced(fn) {
    if (st.offset !== null) fn();
    else st.syncWaiters.push(fn);
  }

  window.setInterval(() => {
    if (st.welcomed && !st.finished && !st.burst && now() - st.lastSync >= SYNC_INTERVAL_MS) {
      startBurst();
    }
  }, 1000);

  // ---------------------------------------------------------------------
  // Assets

  function ensureBuffer(assetId, url, nBytes) {
    if (st.buffers.has(assetId)) return Promise.resolve(st.buffers.get(assetId));
    if (st.loading.has(assetId)) return st.loading.get(assetId);
    const target = url || `/panel/assets/${assetId}.wav`;
    const promise = (async () => {
      let ok = false;
      let buffer = null;
      try {
        if (!ASSET_URL_RE.test(target)) throw new Error("bad asset url");
        const response = await fetch(withKey(target), { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.arrayBuffer();
        if (nBytes !== undefined && data.byteLength !== nBytes) throw new Error("size mismatch");
        if ((await sha256Hex(data)) !== assetId) throw new Error("hash mismatch");
        buffer = await st.ctx.decodeAudioData(data.slice(0));
        st.buffers.set(assetId, buffer);
        ok = true;
      } catch (err) {
        record("asset_failed", `${assetId} ${err}`);
      }
      st.loading.delete(assetId);
      queue({ type: "asset_ready", asset_id: assetId, ok });
      return buffer;
    })();
    st.loading.set(assetId, promise);
    return promise;
  }

  // ---------------------------------------------------------------------
  // Audio scheduling: map a performance.now() time onto the audio clock

  function audioMap() {
    const ctx = st.ctx;
    const ts = typeof ctx.getOutputTimestamp === "function" ? ctx.getOutputTimestamp() : null;
    if (ts && ts.performanceTime > 0 && ts.contextTime > 0) {
      return {
        ctxAt: (perf) => ts.contextTime + (perf - ts.performanceTime) / 1000,
        perfAt: (sec) => ts.performanceTime + (sec - ts.contextTime) * 1000,
      };
    }
    const latency = ctx.outputLatency || ctx.baseLatency || 0;
    const c0 = ctx.currentTime;
    const p0 = now();
    return {
      ctxAt: (perf) => c0 + (perf - p0) / 1000 - latency,
      perfAt: (sec) => p0 + (sec - c0 + latency) * 1000,
    };
  }

  // The sounds a rater must hear before rating (panel.required_plays): the candidate,
  // and the reference when distinguishability is asked.
  function requiredRoles(message) {
    return message.reference && message.ask_distinguishability
      ? ["candidate", "reference"]
      : ["candidate"];
  }

  function notPlayed(slot, role, why) {
    record("not_played", `${slot.id}|${role} ${why}`);
    if (requiredRoles(slot.message).includes(role)) miss(slot, `${role}: ${why}`);
  }

  async function schedulePlay(slot, role, asset, scheduledServerMs, onsetPerf) {
    const key = `${slot.id}|${role}`;
    if (st.played.has(key)) return;
    const buffer = await ensureBuffer(asset.asset_id);
    if (slot.dead || st.finished || st.played.has(key)) return;
    if (slot.mode !== "rate") {
      record("not_played", `${key} slot not rateable`);
      return;
    }
    if (!buffer) {
      notPlayed(slot, role, "asset not ready");
      return;
    }
    if (now() > onsetPerf + MAX_LATE_START_MS) {
      notPlayed(slot, role, "too late");
      return;
    }
    if (st.ctx.state !== "running") {
      // start() on a stopped audio clock returns but plays nothing
      st.ctx.resume().catch((err) => record("resume_error", String(err)));
      notPlayed(slot, role, `audio ${st.ctx.state}`);
      return;
    }
    st.played.add(key);
    persist();
    const source = st.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(st.ctx.destination);
    const map = audioMap();
    let when = map.ctxAt(onsetPerf);
    if (when <= st.ctx.currentTime) when = st.ctx.currentTime; // late: start now
    source.start(when);
    const entry = { node: source, when, cancelled: false };
    slot.sources.push(entry);
    slot.started.add(role);
    const planned = map.perfAt(when);
    record("play", { key, planned });
    // Report once the sound is out, with the output time re-read from the audio clock
    // then (a stalled or drifting device shows up in the logged onset). A start() call
    // is no proof of output: the audio clock must have run past the onset.
    window.setTimeout(() => {
      if (entry.cancelled) {
        record("cancelled", key); // stopped before its onset: it never played
        return;
      }
      if (st.ctx.state !== "running" || st.ctx.currentTime < when) {
        record("not_heard", { key, state: st.ctx.state, currentTime: st.ctx.currentTime, when });
        if (requiredRoles(slot.message).includes(role)) miss(slot, `${role}: audio clock stopped`);
        return;
      }
      const onset = audioMap().perfAt(when);
      record("played", { key, onset });
      queue({
        type: "played",
        rating_slot_id: slot.id,
        role,
        asset_id: asset.asset_id,
        scheduled_server_ms: scheduledServerMs,
        onset_server_ms: Math.max(0, Math.round(onset + st.offset)),
      });
    }, Math.max(0, planned - now()) + REPORT_DELAY_MS);
  }

  // ---------------------------------------------------------------------
  // Slots

  function at(slot, perfTime, fn) {
    const id = window.setTimeout(() => {
      if (!slot.dead) fn();
    }, Math.max(0, perfTime - now()));
    slot.timers.push(id);
  }

  function onSlot(message) {
    const id = message.rating_slot_id;
    if (st.finished || st.slots.has(id) || st.seen.has(id)) {
      record("slot_ignored", id);
      return;
    }
    st.seen.add(id);
    persist();
    const startPerf = message.start_server_ms - st.offset;
    const slot = {
      id,
      message,
      startPerf,
      unlockPerf: startPerf + message.unlock_offset_ms,
      lockPerf: startPerf + SLOT_MS,
      timers: [],
      sources: [],
      started: new Set(),    // roles whose sound started in this page
      mode: "rate",          // rate | placeholder | rejoin | missed
      unlocked: false,
      locked: false,
      submitted: false,
      choice: {},
      dead: false,
    };
    const late = now() - startPerf;
    if (late >= SLOT_MS) {
      record("slot_over", id);
      return;
    }
    st.slots.set(id, slot);
    if (message.placeholder) {
      slot.mode = "placeholder";
    } else if (late > (message.rejoin ? ONSET_TOLERANCE_MS : MAX_LATE_START_MS)) {
      slot.mode = "rejoin";
    }
    record("slot", { id, mode: slot.mode, late });
    if (slot.mode === "rate") {
      // The reference is scheduled only after the candidate: a candidate that cannot
      // play makes the slot neutral, and then nothing else plays in it.
      const candidate = schedulePlay(
        slot, "candidate", message.candidate, message.start_server_ms, startPerf,
      );
      if (message.reference) {
        candidate.then(() => schedulePlay(
          slot, "reference", message.reference,
          message.start_server_ms + REFERENCE_OFFSET_MS, startPerf + REFERENCE_OFFSET_MS,
        ));
      }
      // An asset that never loads (a fetch that hangs) must not leave the slot rateable.
      for (const role of requiredRoles(message)) {
        const onset = role === "candidate" ? startPerf : startPerf + REFERENCE_OFFSET_MS;
        at(slot, onset + MAX_LATE_START_MS, () => {
          if (!slot.started.has(role)) miss(slot, `${role}: not started in time`);
        });
      }
      at(slot, startPerf + REFERENCE_OFFSET_MS, () => revealReference(slot));
      at(slot, slot.unlockPerf, () => unlock(slot));
    }
    at(slot, startPerf, () => activate(slot));
    at(slot, slot.lockPerf, () => lock(slot));
  }

  function activate(slot) {
    if (st.finished) return;
    st.active = slot;
    lockForm();
    if (slot.mode === "placeholder") {
      show("placeholder");
      progress($("placeholder-progress"), slot);
      return;
    }
    if (slot.mode === "rejoin") {
      show("rejoin");
      return;
    }
    if (slot.mode === "missed") {
      show("missed");
      progress($("missed-progress"), slot);
      return;
    }
    const m = slot.message;
    $("meaning").textContent = m.meaning;
    $("reference-card").hidden = false;
    $("reference-meaning").textContent = m.reference ? "The comparison sound follows." : "";
    $("reference-meaning").classList.add("muted");
    $("q-distinguishability").hidden = !m.ask_distinguishability;
    for (const scale of document.querySelectorAll(".scale")) {
      for (const b of scale.querySelectorAll("button")) b.setAttribute("aria-pressed", "false");
    }
    if (now() >= slot.startPerf + REFERENCE_OFFSET_MS) revealReference(slot);
    st.formSlot = slot;
    renderControls(slot);
    show("slot");
    progress($("slot-progress"), slot);
  }

  function lockForm() {
    for (const b of document.querySelectorAll("#rating-form button")) b.disabled = true;
    st.formSlot = null;
  }

  function revealReference(slot) {
    if (st.active !== slot) return;
    const m = slot.message;
    const el = $("reference-meaning");
    if (m.reference) {
      el.textContent = m.reference.meaning;
      el.classList.remove("muted");
    } else {
      el.textContent = "No comparison sound in this slot.";
    }
  }

  // The slot's sound did not reach the rater: neutral screen, no controls, no rating
  // (the host stores the rating as missing). Never undone within the slot.
  function miss(slot, why) {
    if (slot.dead || slot.mode !== "rate") return;
    slot.mode = "missed";
    record("slot_missed", { id: slot.id, why });
    stopSources(slot);
    if (st.active === slot) activate(slot);
  }

  // Stop the slot's sounds; one stopped before its onset never played (no `played`).
  function stopSources(slot) {
    for (const entry of slot.sources) {
      if (st.ctx.currentTime < entry.when) entry.cancelled = true;
      try {
        entry.node.stop();
      } catch (err) {
        record("stop_error", String(err));
      }
    }
  }

  function unlock(slot) {
    slot.unlocked = true;
    if (slot.mode === "rate") {
      const unheard = requiredRoles(slot.message).filter((role) => !slot.started.has(role));
      if (unheard.length) {
        miss(slot, `${unheard.join(",")}: not started by the unlock`);
        return;
      }
    }
    if (st.active === slot) renderControls(slot);
  }

  function lock(slot) {
    slot.locked = true;
    slot.dead = true;
    for (const t of slot.timers) window.clearTimeout(t);
    st.slots.delete(slot.id);
    if (st.formSlot === slot) {
      renderControls(slot);
      lockForm();
    }
    if (st.active !== slot) return;
    st.active = null;
    if (st.finished) return;
    show(st.pause ? "pause" : "wait");
  }

  function complete(slot) {
    const c = slot.choice;
    return Boolean(c.association && c.comfort && (c.distinguishability || !slot.message.ask_distinguishability));
  }

  function renderControls(slot) {
    const open = slot.unlocked && !slot.locked && !slot.submitted && slot.mode === "rate";
    for (const b of document.querySelectorAll("#rating-form .scale button")) b.disabled = !open;
    $("submit-rating").disabled = !(open && complete(slot));
    let text = "Listen.";
    if (slot.submitted) text = slot.ackText || "Sending...";
    else if (slot.locked) text = "Time is up.";
    else if (slot.unlocked) text = "Please rate now.";
    $("slot-status").textContent = text;
  }

  function choose(event) {
    const button = event.target.closest(".scale button");
    const slot = st.active;
    if (!button || !slot || button.disabled) return;
    const scale = button.closest(".scale");
    const name = scale.dataset.name;
    const raw = button.dataset.value;
    slot.choice[name] = name === "comfort" ? raw : Number(raw);
    for (const b of scale.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === button));
    renderControls(slot);
  }

  function submit(event) {
    event.preventDefault();
    const slot = st.active;
    const t = now();
    if (!slot || slot.mode !== "rate" || !slot.unlocked || slot.locked || slot.submitted) return;
    if (t < slot.unlockPerf || t >= slot.lockPerf || !complete(slot)) return;
    const c = slot.choice;
    const message = {
      type: "rating",
      rating_slot_id: slot.id,
      association: c.association,
      distinguishability: slot.message.ask_distinguishability ? c.distinguishability : null,
      comfort: c.comfort,
      rt_ms: Math.max(0, Math.round(t - slot.unlockPerf)),
    };
    slot.submitted = true;
    if (!send(message)) slot.ackText = "Not saved (connection lost).";
    record("rating_sent", slot.id);
    renderControls(slot);
  }

  function onAck(message) {
    record("rating_ack", { id: message.rating_slot_id, accepted: message.accepted, code: message.code });
    const slot = st.slots.get(message.rating_slot_id);
    if (!slot) return;
    slot.ackText = message.accepted ? "Saved. Thank you." : "Not saved.";
    if (st.active === slot) renderControls(slot);
  }

  // ---------------------------------------------------------------------
  // Pause, end, withdrawal

  function onPause(reason) {
    st.pause = reason;
    record("pause", reason);
    if (!st.active) show("pause");
  }

  function onResume() {
    st.pause = null;
    record("resume");
    if (!st.active && !st.finished) show("wait");
  }

  function finish(screen) {
    if (st.finished) return;
    st.finished = true;
    record("finish", screen);
    for (const slot of st.slots.values()) {
      slot.dead = true;
      for (const t of slot.timers) window.clearTimeout(t);
      stopSources(slot);
    }
    st.slots.clear();
    st.active = null;
    $("withdraw-dialog").hidden = true;
    show(screen);
    if (!st.withdrawal) closeSocket();
  }

  function finishWithdrawal(why) {
    record("withdrawal_done", why);
    st.withdrawal = null;
    closeSocket();
  }

  function sendWithdrawal() {
    const w = st.withdrawal;
    if (!w || w.sentOn === st.ws) return;
    if (send({ type: "withdraw", reason: w.reason })) {
      w.sentOn = st.ws;
      record("withdraw_sent", w.reason);
    }
  }

  // The withdrawal is final for the rater at once (withdrawn screen, audio stopped),
  // but the page keeps (re)connecting until the server has answered it, so the host
  // always learns of it (a socket drop must not lose it).
  function withdraw(reason) {
    $("withdraw-dialog").hidden = true;
    if (st.finished) return;
    st.withdrawal = { reason, sentOn: null };
    finish("withdrawn");
    sendWithdrawal();
  }

  function whileWithdrawing(message) {
    const w = st.withdrawal;
    if (message.type === "welcome") {
      st.welcomed = true;
      st.attempt = 0;
      st.sessionId = message.session_id;
      setConnection(true);
      for (const queued of st.outbox.splice(0)) send(queued); // plays that happened before
      sendWithdrawal();
    } else if (message.type === "end" && message.reason === "withdrawn") {
      // the server's answer to this withdrawal (or to a hello of a withdrawn seat)
      if (w.sentOn === st.ws || !st.welcomed) finishWithdrawal("acknowledged");
    } else if (message.type === "error" && message.code === "E_UNKNOWN_RATER") {
      finishWithdrawal("seat refused"); // the session no longer takes this seat
    }
    // anything else (slots, preloads, sync) is ignored after a withdrawal
  }

  // ---------------------------------------------------------------------
  // Start

  async function start() {
    if (!STATION_RE.test(st.station) || !RATER_RE.test(st.rater)) {
      st.station = $("setup-station").value.trim();
      st.rater = $("setup-rater").value.trim();
      if (!STATION_RE.test(st.station) || !RATER_RE.test(st.rater)) {
        $("setup").hidden = false;
        $("start-message").textContent = "Enter the station (S1) and the rater code given by the operator.";
        return;
      }
    }
    if (!window.AudioContext) {
      $("start-message").textContent = "This browser cannot play audio. Please tell the session operator.";
      return;
    }
    $("start-button").disabled = true;
    try {
      st.ctx = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    } catch (err) {
      st.ctx = new AudioContext({ latencyHint: "interactive" });
    }
    // never wait forever for an output device: the slot scheduler copes with a late start
    await Promise.race([st.ctx.resume(), new Promise((resolve) => window.setTimeout(resolve, 1000))]);
    if (st.ctx.state !== "running") record("audio_not_running", st.ctx.state);
    const unlockSource = st.ctx.createBufferSource();
    unlockSource.buffer = st.ctx.createBuffer(1, 1, st.ctx.sampleRate);
    unlockSource.connect(st.ctx.destination);
    unlockSource.start();
    $("station-label").textContent = `Station ${st.station}`;
    show("wait");
    connect();
  }

  function init() {
    if (!STATION_RE.test(st.station) || !RATER_RE.test(st.rater)) $("setup").hidden = false;
    $("start-button").addEventListener("click", start);
    $("rating-form").addEventListener("click", choose);
    $("rating-form").addEventListener("submit", submit);
    $("withdraw-button").addEventListener("click", () => {
      $("withdraw-dialog").hidden = false;
    });
    $("withdraw-cancel").addEventListener("click", () => {
      $("withdraw-dialog").hidden = true;
    });
    for (const b of document.querySelectorAll("#withdraw-dialog [data-reason]")) {
      b.addEventListener("click", () => withdraw(b.dataset.reason));
    }
    setConnection(false);
    show("start");
  }

  // Read-only view for the operator and tests; nothing here plays audio.
  window.avStation = Object.freeze({
    sha256Hex,
    sha256HexJs,
    state: () => ({
      station: st.station,
      rater: st.rater,
      connected: st.welcomed,
      offset: st.offset,
      rtt: st.rtt,
      active: st.active ? st.active.id : null,
      mode: st.active ? st.active.mode : null,
      pause: st.pause,
      finished: st.finished,
      withdrawalPending: st.withdrawal !== null,
      buffers: st.buffers.size,
      played: Array.from(st.played),
      log: st.log.slice(),
    }),
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

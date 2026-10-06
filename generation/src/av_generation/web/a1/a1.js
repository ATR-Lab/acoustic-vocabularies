// A1 designer screen (#19). Plain JavaScript, no dependencies, same-origin only.
//
// The server owns every rule: slots, the 40-s timer, validation, the ledger and the
// single-use audio tokens. This page only offers constrained controls (radio groups and
// range inputs built from the server's domain), shows the countdowns, plays each served
// waveform exactly once through Web Audio (no media element, nothing to replay) and
// reports focus/idle changes for the design-time log.
"use strict";

(function () {
  const API = "/a1/api";
  const POLL_MS = 400;
  const IDLE_MS = 15000;
  const FIELDS = [
    { field: "total_ms", label: "Total duration (ms)", count: 1, kind: "choice" },
    { field: "pitches", label: "Pitches (semitones)", count: 3, kind: "range" },
    { field: "rhythm_weights", label: "Rhythm weights", count: 3, kind: "choice" },
    { field: "gaps_ms", label: "Gaps (ms)", count: 2, kind: "choice" },
    { field: "amplitudes", label: "Amplitudes", count: 3, kind: "choice", decimals: 1 },
  ];
  const DEFAULTS = {
    total_ms: [600],
    pitches: [0, 0, 0],
    rhythm_weights: [2, 2, 2],
    gaps_ms: [40, 40],
    amplitudes: [0.8, 0.8, 0.8],
  };

  const $ = (id) => document.getElementById(id);
  const debug = (window.a1Debug = { plays: 0, playErrors: [], submits: 0 });

  let state = null;
  let stateAt = 0;
  let formBuilt = false;
  let submitting = false;
  let audioCtx = null;
  let lastKey = "";
  let lastWindowKey = "";
  let active = true;
  let lastInput = performance.now();

  // ------------------------------------------------------------------ HTTP

  async function call(method, path, body) {
    const options = { method, cache: "no-store", headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const res = await fetch(API + path, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = (data && data.error) || { code: "E_HTTP", message: res.statusText };
      const error = new Error(err.message);
      error.code = err.code;
      throw error;
    }
    return data;
  }

  function setStatus(text, tone) {
    const el = $("status");
    el.textContent = text;
    el.className = "status" + (tone ? " " + tone : "");
  }

  // ------------------------------------------------------------------ form

  function choiceGroup(name, values, current, decimals) {
    const wrap = document.createElement("span");
    wrap.className = "choices";
    wrap.setAttribute("role", "radiogroup");
    for (const v of values) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = name;
      input.value = decimals ? Number(v).toFixed(decimals) : String(v);
      input.checked = Number(v) === Number(current);
      input.addEventListener("change", renderPreview);
      const span = document.createElement("span");
      span.textContent = input.value;
      label.append(input, span);
      wrap.append(label);
    }
    return wrap;
  }

  function rangeInput(name, values, current) {
    const wrap = document.createElement("span");
    wrap.className = "range";
    const input = document.createElement("input");
    input.type = "range";
    input.name = name;
    input.min = String(Math.min(...values));
    input.max = String(Math.max(...values));
    input.step = "1";
    input.value = String(current);
    const out = document.createElement("output");
    out.className = "pitch-value";
    const show = () => {
      const n = Number(input.value);
      out.textContent = (n > 0 ? "+" : "") + n;
    };
    input.addEventListener("input", () => {
      show();
      renderPreview();
    });
    show();
    wrap.append(input, out);
    return wrap;
  }

  function buildForm(domain) {
    const form = $("recipe-form");
    form.textContent = "";
    for (const spec of FIELDS) {
      const fs = document.createElement("fieldset");
      fs.dataset.field = spec.field;
      const legend = document.createElement("legend");
      legend.textContent = spec.label;
      fs.append(legend);
      for (let i = 0; i < spec.count; i++) {
        const row = document.createElement("div");
        row.className = "row";
        const label = document.createElement("span");
        label.className = "row-label";
        label.textContent =
          spec.count > 1 ? (spec.field === "gaps_ms" ? "Gap " : "Event ") + (i + 1) : "Total";
        const name = spec.field + "-" + i;
        const values = domain[spec.field];
        const current = DEFAULTS[spec.field][i];
        row.append(
          label,
          spec.kind === "range"
            ? rangeInput(name, values, current)
            : choiceGroup(name, values, current, spec.decimals)
        );
        fs.append(row);
      }
      form.append(fs);
    }
    formBuilt = true;
    renderPreview();
  }

  function readForm() {
    const recipe = {};
    for (const spec of FIELDS) {
      const vals = [];
      for (let i = 0; i < spec.count; i++) {
        const name = spec.field + "-" + i;
        let el;
        if (spec.kind === "range") {
          el = document.querySelector(`input[name="${name}"]`);
        } else {
          el = document.querySelector(`input[name="${name}"]:checked`);
        }
        vals.push(el ? Number(el.value) : null);
      }
      recipe[spec.field] = spec.count === 1 ? vals[0] : vals;
    }
    return recipe;
  }

  function loadRecipe(recipe) {
    if (!recipe) return;
    for (const spec of FIELDS) {
      const vals = spec.count === 1 ? [recipe[spec.field]] : recipe[spec.field];
      vals.forEach((v, i) => {
        const name = spec.field + "-" + i;
        if (spec.kind === "range") {
          const el = document.querySelector(`input[name="${name}"]`);
          if (el) {
            el.value = String(v);
            el.dispatchEvent(new Event("input"));
          }
        } else {
          for (const el of document.querySelectorAll(`input[name="${name}"]`)) {
            el.checked = Number(el.value) === Number(v);
          }
        }
      });
    }
    renderPreview();
  }

  function setFormEnabled(enabled) {
    for (const fs of document.querySelectorAll("#recipe-form fieldset")) fs.disabled = !enabled;
  }

  // Schematic only: event durations follow the schema arithmetic (Study A protocol §3.2:
  // gaps are subtracted from total_ms, the rest is split by the rhythm weights). It shows
  // the entered parameters; it does not check admissibility.
  function renderPreview() {
    const svg = $("preview");
    const r = readForm();
    svg.textContent = "";
    const ns = "http://www.w3.org/2000/svg";
    const add = (tag, attrs, text) => {
      const el = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
      if (text !== undefined) el.textContent = text;
      svg.append(el);
      return el;
    };
    add("line", { x1: 0, y1: 70, x2: 900, y2: 70, class: "axis" });
    if (r.total_ms == null || r.gaps_ms.includes(null) || r.rhythm_weights.includes(null)) return;
    const sum = r.rhythm_weights.reduce((a, b) => a + b, 0);
    const body = r.total_ms - r.gaps_ms[0] - r.gaps_ms[1];
    const durs = r.rhythm_weights.map((w) => (body * w) / sum);
    let x = 0;
    durs.forEach((d, j) => {
      const p = r.pitches[j] == null ? 0 : r.pitches[j];
      const y = 70 - p * 9 - 7;
      const amp = r.amplitudes[j] == null ? 1 : r.amplitudes[j];
      add("rect", { x, y, width: Math.max(1, d), height: 14, rx: 3, class: "bar", "fill-opacity": amp });
      add("text", { x: x + 2, y: 134 }, `${d.toFixed(1)} ms`);
      x += d + (j < 2 ? r.gaps_ms[j] : 0);
    });
    $("preview-caption").textContent =
      `Schematic: ${r.total_ms} ms in total; event durations ${durs.map((d) => d.toFixed(1)).join(" / ")} ms (no sound).`;
  }

  // ------------------------------------------------------------------ audio

  function ensureAudio() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx) audioCtx = new Ctx();
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  async function playOnce(audio) {
    const ctx = ensureAudio();
    const res = await fetch(audio.url, { cache: "no-store" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const err = new Error((data.error && data.error.message) || res.statusText);
      err.code = data.error && data.error.code;
      throw err;
    }
    let bytes = await res.arrayBuffer();
    debug.plays += 1;
    if (!ctx) return;
    const buffer = await ctx.decodeAudioData(bytes);
    bytes = null;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    src.start();
    src.onended = () => src.disconnect();
  }

  // ------------------------------------------------------------------ actions

  async function openSlot() {
    $("open-slot").disabled = true;
    try {
      const slot = await call("POST", "/slots/open");
      active = true;
      lastInput = performance.now();
      setStatus(`Slot ${slot.slot} is open. Set the recipe, then Submit and play.`);
    } catch (err) {
      setStatus(`${err.code}: ${err.message}`, "bad");
    }
    await refresh(true);
  }

  async function submit() {
    if (submitting || !state || !state.window) return;
    const open = state.window.slots.find((s) => s.state === "open");
    if (!open) return;
    submitting = true;
    $("submit").disabled = true;
    ensureAudio(); // inside the click: lets the page start audio
    const recipe = readForm();
    try {
      debug.submits += 1;
      const res = await call("POST", `/slots/${encodeURIComponent(open.slot_id)}/submit`, { recipe });
      if (res.valid && res.audio) {
        setStatus(`Slot ${res.slot}: valid. Playing once...`, "ok");
        try {
          await playOnce(res.audio);
          setStatus(`Slot ${res.slot}: valid, played once. The slot is closed.`, "ok");
        } catch (err) {
          debug.playErrors.push(String(err.code || err.message));
          setStatus(`Slot ${res.slot}: valid, but playback failed (${err.code || err.message}).`, "bad");
        }
      } else {
        const why = res.validator_messages.length ? res.validator_messages.join("; ") : res.outcome;
        setStatus(`Slot ${res.slot}: ${res.outcome}. ${why}`, "bad");
      }
    } catch (err) {
      setStatus(`${err.code}: ${err.message}`, "bad");
    } finally {
      submitting = false;
    }
    await refresh(true);
  }

  async function playPending(slot) {
    ensureAudio();
    try {
      await playOnce(slot.audio);
      setStatus(`Slot ${slot.slot}: played once.`, "ok");
    } catch (err) {
      setStatus(`${err.code || "E_AUDIO"}: ${err.message}`, "bad");
    }
    await refresh(true);
  }

  async function report(kind) {
    try {
      return await call("POST", "/activity", { kind });
    } catch (err) {
      return null;
    }
  }

  function markActive() {
    lastInput = performance.now();
    if (!active) {
      active = true;
      report("active");
    }
  }

  function markIdle() {
    if (active) {
      active = false;
      report("idle");
    }
  }

  async function toggleFamiliarization() {
    const running = state && state.familiarization.running;
    const res = await report(running ? "familiarization_end" : "familiarization_start");
    if (res === null) setStatus("Familiarization cannot start while a slot is open.", "bad");
    await refresh(true);
  }

  // ------------------------------------------------------------------ rendering

  const fmtS = (ms) => (ms == null ? "--" : (Math.max(0, ms) / 1000).toFixed(1) + " s");
  const fmtClock = (ms) => {
    const s = Math.max(0, Math.round(ms / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  };
  const recipeText = (r) =>
    r
      ? `${r.total_ms} | ${r.pitches.join(",")} | ${r.rhythm_weights.join(",")} | ${r.gaps_ms.join(",")} | ` +
        r.amplitudes.map((a) => Number(a).toFixed(1)).join(",")
      : "-";

  function cell(text, cls) {
    const td = document.createElement("td");
    td.textContent = text;
    if (cls) td.className = cls;
    return td;
  }

  function tag(text, tone) {
    const span = document.createElement("span");
    span.className = "tag " + tone;
    span.textContent = text;
    return span;
  }

  function useButton(recipe) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "small use-recipe";
    btn.textContent = "Load";
    btn.title = "Load this recipe into the form (while a slot is open)";
    btn.disabled = !(state && state.window && state.window.slots.some((s) => s.state === "open"));
    btn.addEventListener("click", () => loadRecipe(recipe));
    return btn;
  }

  function renderSlots() {
    const list = $("slots");
    list.textContent = "";
    const w = state.window;
    if (!w) return;
    for (const s of w.slots) {
      const li = document.createElement("li");
      const valid = s.outcome === "valid";
      li.className = `slot ${s.state}` + (s.state === "closed" ? (valid ? " valid" : " invalid") : "");
      li.dataset.slot = String(s.slot);
      li.dataset.state = s.state;
      const name = document.createElement("div");
      name.className = "slot-name";
      name.textContent = `Slot ${s.slot}`;
      const st = document.createElement("div");
      st.className = "slot-state";
      st.textContent =
        s.state === "open"
          ? "open"
          : s.state === "closed"
            ? s.outcome
            : s.state === "refused"
              ? "refused by the ledger"
              : "not opened";
      li.append(name, st);
      if (s.audio) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "small play-once";
        btn.textContent = "Play once";
        btn.addEventListener("click", () => playPending(s));
        li.append(btn);
      }
      list.append(li);
    }
  }

  function renderHeader() {
    const w = state.window;
    document.body.classList.toggle("practice", state.mode === "practice");
    $("practice-banner").hidden = state.mode !== "practice";
    if (!w) {
      $("atom-line").textContent = "Waiting for the first round";
      $("meaning").textContent = "No proposal window is open";
      return;
    }
    const label = w.label ? ` - ${w.label}` : "";
    $("atom-line").textContent = `Atom ${w.atom_id}${label}` + (w.open ? "" : " (round closed)");
    $("meaning").textContent = w.meaning;
    $("round").textContent = `${w.round} of 4`;
    $("profile").textContent = `${w.profile} (${w.f0_hz} Hz)`;
    $("slots-used").textContent = `${w.atom_slots_used} of ${w.atom_slots_cap}`;
  }

  function renderFeedback(fb) {
    const box = $("feedback");
    box.textContent = "";
    if (!fb.available || (fb.candidates.length === 0 && fb.current_round.length === 0)) {
      box.append(Object.assign(document.createElement("p"), { className: "muted", textContent: "No feedback yet." }));
      return;
    }
    if (!fb.ratings_shown) {
      box.append(
        Object.assign(document.createElement("p"), {
          className: "hint",
          textContent: "Practice mode: technical status only (no panel ratings).",
        })
      );
    }
    const table = document.createElement("table");
    table.id = "feedback-table";
    const head = document.createElement("tr");
    for (const h of ["R.S", "Recipe (T | pitches | weights | gaps | amps)", "Status", "Ratings", "Eligible", "Score", ""]) {
      const th = document.createElement("th");
      th.textContent = h;
      head.append(th);
    }
    table.append(head);
    for (const c of fb.candidates) {
      const tr = document.createElement("tr");
      if (c.incumbent) tr.className = "incumbent";
      const status = document.createElement("td");
      status.append(tag(c.outcome, c.outcome === "valid" ? "ok" : "bad"));
      if (c.incumbent) status.append(" ", tag("incumbent", "star"));
      const ratings = c.ratings.length
        ? c.ratings.map((r) => `${r.association ?? "-"}/${r.distinguishability ?? "-"}/${r.comfort ? r.comfort[0] : "-"}`).join("\n")
        : "-";
      const use = document.createElement("td");
      if (c.recipe) use.append(useButton(c.recipe));
      tr.append(
        cell(`${c.round}.${c.slot}`, "num"),
        cell(recipeText(c.recipe), "mono"),
        status,
        cell(ratings, "ratings mono"),
        cell(c.eligible == null ? "-" : c.eligible ? "yes" : "no"),
        cell(c.score_value == null ? "-" : c.score_value.toFixed(2), "num"),
        use
      );
      table.append(tr);
    }
    for (const c of fb.current_round) {
      const tr = document.createElement("tr");
      tr.className = "pending";
      const status = document.createElement("td");
      status.append(tag(c.outcome, c.outcome === "valid" ? "ok" : "bad"), " ", tag("awaiting ratings", "wait"));
      const use = document.createElement("td");
      if (c.recipe) use.append(useButton(c.recipe));
      tr.append(cell(`${c.round}.${c.slot}`, "num"), cell(recipeText(c.recipe), "mono"), status, cell("-"), cell("-"), cell("-"), use);
      table.append(tr);
    }
    box.append(table);
  }

  function renderBook(book) {
    const box = $("book");
    box.textContent = "";
    if (!book.available || book.committed.length === 0) {
      box.append(Object.assign(document.createElement("p"), { className: "muted", textContent: "No committed atoms yet." }));
      return;
    }
    const table = document.createElement("table");
    table.id = "book-table";
    const head = document.createElement("tr");
    for (const h of ["Atom", "Meaning", "Recipe (T | pitches | weights | gaps | amps)"]) {
      const th = document.createElement("th");
      th.textContent = h;
      head.append(th);
    }
    table.append(head);
    for (const a of book.committed) {
      const tr = document.createElement("tr");
      tr.append(
        cell(a.atom_id + (a.label ? ` (${a.label})` : "")),
        cell(a.meaning || "-"),
        cell(recipeText(a.recipe), "mono")
      );
      table.append(tr);
    }
    box.append(table);
  }

  function renderTimers() {
    if (!state) return;
    const elapsed = performance.now() - stateAt;
    const w = state.window;
    const open = w ? w.slots.find((s) => s.state === "open") : null;
    const cd = $("countdown");
    if (open && open.remaining_ms != null) {
      const left = open.remaining_ms - elapsed;
      $("countdown-value").textContent = fmtS(left);
      cd.classList.toggle("low", left < 10000);
    } else {
      $("countdown-value").textContent = "--";
      cd.classList.remove("low");
    }
    $("window-left").textContent = w && w.open ? fmtClock(w.window_remaining_ms - elapsed) : "-";
    const fam = state.familiarization;
    $("fam-time").textContent = fmtClock(fam.total_ms + (fam.running ? elapsed : 0));
  }

  function renderControls() {
    const w = state.window;
    const open = w ? w.slots.find((s) => s.state === "open") : null;
    const canOpen = !!(w && w.open && !open && w.slots.some((s) => s.state === "unopened"));
    $("open-slot").disabled = !canOpen;
    $("submit").disabled = !open || submitting;
    setFormEnabled(!!open && !submitting);
    for (const b of document.querySelectorAll(".use-recipe")) b.disabled = !open;
    const fam = state.familiarization;
    $("fam-toggle").textContent = fam.running ? "End familiarization" : "Start familiarization";
    $("fam-toggle").disabled = !!open;
    const windowKey = w ? `${w.atom_id}|${w.round}` : "none";
    if (windowKey !== lastWindowKey) {
      lastWindowKey = windowKey;
      if (!w) setStatus("Waiting for the round to start.");
      else if (w.open) setStatus(`Round ${w.round} of atom ${w.atom_id}: open a slot when ready.`);
    }
  }

  async function refresh(force) {
    let next;
    try {
      next = await call("GET", "/state");
    } catch (err) {
      setStatus("Connection to the server lost; retrying...", "bad");
      return;
    }
    state = next;
    stateAt = performance.now();
    if (!formBuilt) buildForm(state.domain);
    const w = state.window;
    const key = w ? [w.atom_id, w.round, w.open, w.slots.map((s) => s.state + s.outcome).join()].join("|") : "none";
    renderHeader();
    renderSlots();
    renderControls();
    renderTimers();
    if (force || key !== lastKey) {
      lastKey = key;
      const [fb, book] = await Promise.all([call("GET", "/feedback"), call("GET", "/book")]).catch(() => [null, null]);
      if (fb) renderFeedback(fb);
      if (book) renderBook(book);
      renderControls();
    }
  }

  // ------------------------------------------------------------------ start

  function start() {
    $("open-slot").addEventListener("click", openSlot);
    $("submit").addEventListener("click", submit);
    $("fam-toggle").addEventListener("click", toggleFamiliarization);
    for (const ev of ["pointerdown", "keydown", "input", "wheel"]) {
      document.addEventListener(ev, markActive, { passive: true });
    }
    window.addEventListener("focus", markActive);
    window.addEventListener("blur", markIdle);
    document.addEventListener("visibilitychange", () => (document.hidden ? markIdle() : markActive()));
    document.addEventListener("contextmenu", (e) => e.preventDefault());
    setInterval(() => {
      if (active && performance.now() - lastInput > IDLE_MS) markIdle();
    }, 1000);
    setInterval(() => refresh(false), POLL_MS);
    setInterval(renderTimers, 100);
    refresh(true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

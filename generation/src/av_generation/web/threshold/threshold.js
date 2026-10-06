// Separation-threshold listening page (#23). Plain JavaScript, no third-party code.
// One trial: Play -> motif A, gap, motif B (Web Audio clock) -> Same / Different.
// Each pair plays once (single-use audio URLs); answers are never scored on this page.
// Onsets, the opening of the answer buttons and the RT zero point are times at the
// speaker: the AudioContext output latency is added and reported to the server.
// The play is reported only after motif B has ended (its source fired `ended` and the
// end has reached the speaker), so a reload or crash during playback leaves the trial
// unreported, which the server makes skip-only: every answer follows both complete motifs.
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const LEAD_S = 0.15; // scheduling lead before motif A starts
  const STALL_MS = 5000; // motif B not ended this long after its planned end: audio failed
  let ctx = null;
  let trial = null; // {index, endPerf, reported}

  async function api(method, path, body) {
    const init = { method, cache: "no-store", headers: {} };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.detail || res.statusText);
      err.code = data.error || String(res.status);
      throw err;
    }
    return data;
  }

  async function fetchAudio(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      const err = new Error("audio unavailable");
      err.code = "E_AUDIO";
      throw err;
    }
    return ctx.decodeAudioData(await res.arrayBuffer());
  }

  function setProgress(state) {
    $("progress").textContent =
      state.status === "done"
        ? `All ${state.n_trials} trials done.`
        : `Trial ${state.n_done + 1} of ${state.n_trials}`;
  }

  function finish(state) {
    setProgress(state);
    $("play").hidden = true;
    $("answer").hidden = true;
    $("skip").hidden = true;
    $("status").textContent = "Thank you. The session is complete.";
    document.body.dataset.state = "done";
  }

  function ready(state) {
    setProgress(state);
    trial = null;
    $("answer").hidden = true;
    $("skip").hidden = true;
    $("play").hidden = false;
    $("play").disabled = false;
    $("status").textContent = "Press Play when you are ready.";
    document.body.dataset.state = "ready";
  }

  function openAnswer(endPerf) {
    trial.endPerf = endPerf;
    $("play").hidden = true;
    $("answer").hidden = false;
    $("same").disabled = false;
    $("different").disabled = false;
    $("status").textContent = "Were the two sounds the same or different?";
    document.body.dataset.state = "answer";
  }

  function interrupted(index) {
    // The audio of this trial left the server before an interruption (reload or server
    // restart) and its play was never logged: it is not played again.
    trial = { index, endPerf: null, reported: null };
    $("play").hidden = true;
    $("answer").hidden = true;
    $("status").textContent = "This pair was interrupted and cannot be played again. Please call the operator.";
    $("skip").hidden = false;
    document.body.dataset.state = "error";
  }

  function fail(err) {
    $("play").hidden = true;
    $("answer").hidden = true;
    $("status").textContent = `Audio could not be played (${err.code || "error"}). Please call the operator.`;
    $("skip").hidden = trial === null;
    document.body.dataset.state = "error";
  }

  function play(buffer, when) {
    const source = ctx.createBufferSource();
    source.buffer = buffer; // no gain node: the station's fixed output gain applies
    source.connect(ctx.destination);
    source.start(when);
    return source;
  }

  // Resolves when `source` has played to its end and `endPerf` (that end at the speaker)
  // has passed; rejects (E_AUDIO_STALLED) when that has not happened STALL_MS later.
  function heardToEnd(source, endPerf) {
    return new Promise((resolve, reject) => {
      let ended = false;
      let reached = false;
      const wait = () => Math.max(0, endPerf - performance.now());
      const done = () => {
        if (ended && reached) {
          clearTimeout(watchdog);
          resolve();
        }
      };
      const watchdog = setTimeout(() => {
        const err = new Error("motif B did not finish playing");
        err.code = "E_AUDIO_STALLED";
        reject(err);
      }, wait() + STALL_MS);
      source.addEventListener("ended", () => {
        ended = true;
        done();
      }, { once: true });
      setTimeout(() => {
        reached = true;
        done();
      }, wait());
    });
  }

  // Maps AudioContext times to performance.now() times: `scheduled` without and `heard`
  // with the output latency (getOutputTimestamp when the browser has it, otherwise
  // outputLatency or baseLatency).
  function clockMap() {
    const ctxNow = ctx.currentTime;
    const base = performance.now() - ctxNow * 1000;
    let heard = null;
    if (typeof ctx.getOutputTimestamp === "function") {
      const ts = ctx.getOutputTimestamp();
      if (ts && ts.contextTime > 0 && ts.performanceTime > 0) {
        heard = ts.performanceTime - ts.contextTime * 1000;
      }
    }
    if (heard === null || heard < base || heard - base > 1000) {
      heard = base + 1000 * (ctx.outputLatency || ctx.baseLatency || 0);
    }
    return {
      heard: (t) => heard + t * 1000,
      latencyMs: Math.max(0, Math.round(heard - base)),
    };
  }

  async function startTrial() {
    $("play").disabled = true;
    $("play").hidden = true;
    $("status").textContent = "Listen.";
    document.body.dataset.state = "listening";
    const next = await api("POST", "/threshold/api/next");
    const received = performance.now();
    trial = { index: next.trial_index, endPerf: null, reported: null };
    if (next.phase === "respond") {
      openAnswer(null); // heard to the end before a reload: answer without replay
      return;
    }
    if (next.phase === "skip") {
      interrupted(next.trial_index);
      return;
    }
    if (ctx === null) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
    }
    await ctx.resume();
    const [first, second] = await Promise.all([fetchAudio(next.first), fetchAudio(next.second)]);
    const onsetFirst = ctx.currentTime + LEAD_S;
    const onsetSecond = onsetFirst + first.duration + next.gap_ms / 1000;
    const endSecond = onsetSecond + second.duration;
    play(first, onsetFirst);
    const sourceB = play(second, onsetSecond);
    const clock = clockMap();
    const endPerf = clock.heard(endSecond);
    const body = {
      onset_first_ms: Math.max(0, Math.round(clock.heard(onsetFirst) - received)),
      onset_second_ms: Math.max(0, Math.round(clock.heard(onsetSecond) - received)),
      output_latency_ms: clock.latencyMs,
    };
    // Report only once both motifs have been heard to the end. Until then the server
    // knows only that the audio was delivered, so an interruption makes it skip-only.
    await heardToEnd(sourceB, endPerf);
    trial.reported = api("POST", `/threshold/api/trials/${trial.index}/played`, body);
    openAnswer(endPerf); // an answer is sent only after the play report succeeded
    await trial.reported;
  }

  async function answer(response) {
    $("same").disabled = true;
    $("different").disabled = true;
    const rt = trial.endPerf === null ? null : Math.max(0, Math.round(performance.now() - trial.endPerf));
    await trial.reported;
    const state = await api("POST", `/threshold/api/trials/${trial.index}/response`, {
      response,
      rt_ms: rt,
    });
    if (state.status === "done") finish(state);
    else ready(state);
  }

  async function skip() {
    $("skip").disabled = true;
    const state = await api("POST", `/threshold/api/trials/${trial.index}/skip`);
    $("skip").disabled = false;
    if (state.status === "done") finish(state);
    else ready(state);
  }

  async function load() {
    const state = await api("GET", "/threshold/api/state");
    if (state.status === "done") finish(state);
    else if (state.phase === "skip") {
      setProgress(state);
      interrupted(state.trial_index);
    } else ready(state);
  }

  $("play").addEventListener("click", () => startTrial().catch(fail));
  $("same").addEventListener("click", () => answer("same").catch(fail));
  $("different").addEventListener("click", () => answer("different").catch(fail));
  $("skip").addEventListener("click", () => skip().catch(fail));
  load().catch(fail);
})();

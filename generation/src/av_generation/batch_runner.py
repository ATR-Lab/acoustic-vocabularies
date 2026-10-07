"""Study-mode batch runner (#20): one Study A batch built from the real components.

`Orchestrator` (#20) takes its proposers and panel from the caller. This module builds
them for a batch run and serves what people use during it:

- one shared slot ledger (`ledger.SlotLedger`, #17) on the run's `slots.jsonl`, with the
  run's refusal and timing logs;
- A3 (`a3.A3Proposer`, #17) with the prompt set (`prompts`, #17) and the OpenAI-compatible
  client (`llm.OpenAICompatibleClient`, #16) of the LLM server at `llm_url`, logging
  every call to `llm-requests.jsonl`;
- A2 (`a2.A2Proposer`, #18) on the same ledger;
- A1 (`a1.study_service`, #19) on the same ledger and the run's play, timing and refusal
  logs, served to the designer's kiosk with `a1.serve_a1` while the batch runs; its
  slots open back to back (#19);
- the rater panel: the panel server (`panel.create_panel_app`, #21) over the
  orchestrator's `PanelSessionHost`, served by `serve_panel`.

Synthetic runs (`demo`/`synthetic` kinds only) can swap in the stand-ins of
`_batch_sim`: simulated proposers (`proposers="sim"`), a bot designer that works the
real A1 app over HTTP (`designer="bot"`) and in-process bot raters on the panel session
contract (`panel="bots"`). The accelerated tests and the dry run (#22) use them.

Start checks: `check_batch_start` is the one function every batch run passes before
anything is created (run ID, input pins, prompt/LLM/meaning hashes, the frozen-config rule
of `genconfig.check_run_config`); the G4 freeze guard (#25) belongs there.

Command line (from the repository root):

    uv run --project generation python -m av_generation.batch_runner run \\
        --run-dir <restricted runs>/<run_id> --kind pilot --config <config.json> \\
        --generation-config <generation-config.json> --meanings <meanings dir> \\
        --fallback <fallback manifest> --llm-url http://<llm-host>:8000 \\
        --a1-host <lab interface> --panel-host <lab interface> --appointment next

`check` runs the start checks only. `generation/docs/orchestrator.md` (section 9) has the
operator steps.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import httpx
from av_sound._paths import data_root as sound_root
from av_sound.fallback import FallbackSet, load_fallback
from av_sound.store import VocabularyStore

from av_generation import _batch_sim as sim
from av_generation._paths import examples_path
from av_generation.a1 import DEFAULT_PORT as A1_PORT
from av_generation.a1 import A1SlotService, serve_a1, study_service
from av_generation.a2 import A2Proposer
from av_generation.a3 import A3Proposer
from av_generation.clock import Clock, ManualClock, ScaledClock, SystemClock
from av_generation.config import BatchConfig
from av_generation.constants import APPOINTMENTS_PER_BATCH, ATOMS_PER_APPOINTMENT
from av_generation.genconfig import (
    GenerationConfig,
    build_generation_config,
    check_run_config,
    fallback_pins,
)
from av_generation.ids import PUBLIC_RUN_KINDS, Method, RunKind
from av_generation.jsonio import read_json
from av_generation.ledger import SlotLedger
from av_generation.llm import OpenAICompatibleClient, decoding_schema, decoding_schema_sha256
from av_generation.llm_manifest import (
    LlmManifest,
    load_llm_manifest,
    manifest_sha256,
)
from av_generation.meanings import MeaningSet, load_meanings
from av_generation.mock_llm import MOCK_NAME, MOCK_RUNTIME, MOCK_VERSION
from av_generation.orchestrator import (
    SLOT_EVENT_LEAD_MS,
    BatchIncomplete,
    Orchestrator,
    check_batch_pins,
)
from av_generation.panel import create_panel_app
from av_generation.panel_session import PanelSessionHost
from av_generation.prompts import PromptSet, default_prompt_set_dir, load_prompt_set
from av_generation.proposers import RoundProposer
from av_generation.rater_protocol import STATION_PAGE
from av_generation.records import RecordWriter
from av_generation.rundir import (
    MANIFEST_NAME,
    RunLayout,
    check_run_id,
    check_run_location,
    create_run_dir,
)
from av_generation.webserve import serve_in_thread

__all__ = [
    "BatchInputs",
    "DEMO_GENERATION_CONFIG_NAME",
    "PANEL_PORT",
    "RunnerError",
    "StudyBatch",
    "check_batch_start",
    "demo_generation_config",
    "load_batch_inputs",
    "main",
    "open_batch",
    "probe_llm_server",
    "run_session",
    "serve_panel",
    "wait_for_stations",
]

ProposerMode = Literal["real", "sim"]
PanelMode = Literal["stations", "bots"]
DesignerMode = Literal["kiosk", "bot"]
Appointment = int | Literal["next", "all"]

PANEL_PORT: Final = 8765
"""Default port of the rater panel server (the stations' URL)."""
STATION_TIMEOUT_S: Final = 600.0
"""Default real seconds to wait for every seat's station before the session starts."""
DEMO_GENERATION_CONFIG_NAME: Final = "DEMO-batch-runner"
"""Name of the generation config built for a demo/synthetic run without one."""

E_INPUTS: Final = "E_INPUTS"
E_MODE: Final = "E_MODE"
E_LLM_SERVER: Final = "E_LLM_SERVER"
E_RUN_DIR: Final = "E_RUN_DIR"
E_STATIONS: Final = "E_STATIONS"


class RunnerError(RuntimeError):
    """The batch runner refuses to start or continue; `.code` names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Inputs and start checks


@dataclass(frozen=True, slots=True)
class BatchInputs:
    """Everything a batch run reads before it starts (restricted unless DEMO)."""

    config: BatchConfig
    generation_config: GenerationConfig
    meanings: MeaningSet
    fallback: FallbackSet
    prompt_set: PromptSet | None = None
    """A3's prompt set (#17); `None` with simulated proposers."""
    llm_manifest: LlmManifest | None = None
    """The LLM manifest (#16) the server must match; `None` with simulated proposers."""
    llm_manifest_sha256: str | None = None
    """File SHA-256 of the LLM manifest (`llm_manifest.manifest_sha256`)."""
    freeze_manifest: Mapping[str, Any] | None = None
    """The G4 freeze manifest (#25); required for confirmatory runs."""
    freeze_manifest_path: Path | None = None


def demo_generation_config(
    config: BatchConfig,
    meanings: MeaningSet,
    fallback: FallbackSet,
    *,
    prompt_set: PromptSet | None = None,
    llm_manifest_sha256: str | None = None,
) -> GenerationConfig:
    """The generation config of a demo/synthetic run that brings none: the running code,
    the inputs' hashes and, for real proposers (`prompt_set` given), the decoding schema of
    #16 and the prompt-set hashes of #17. Without a prompt set it is the simulated
    proposers' config (`_batch_sim.demo_generation_config`)."""
    if prompt_set is None:
        return sim.demo_generation_config(meanings, fallback, threshold=config.threshold)
    return build_generation_config(
        DEMO_GENERATION_CONFIG_NAME,
        llm_manifest_sha256=llm_manifest_sha256,
        decoding_schema_sha256=decoding_schema_sha256(),
        prompts=prompt_set.hashes(),
        meanings_sha256=meanings.sha256(),
        separation_threshold=config.threshold,
        fallback=fallback_pins(fallback),
    )


def load_batch_inputs(
    *,
    config: str | os.PathLike[str],
    meanings: str | os.PathLike[str],
    fallback: str | os.PathLike[str],
    generation_config: str | os.PathLike[str] | None = None,
    prompts: str | os.PathLike[str] | None = None,
    llm_manifest: str | os.PathLike[str] | None = None,
    freeze_manifest: str | os.PathLike[str] | None = None,
    proposers: ProposerMode = "real",
) -> BatchInputs:
    """Read a batch's input files (restricted unless DEMO).

    `prompts` defaults to the committed prompt set (`generation/prompts/`) and
    `llm_manifest` to the committed LLM manifest; both are read for real proposers only.
    Without `generation_config`, a demo batch gets `demo_generation_config`; pilot and
    confirmatory batches must bring theirs (`E_INPUTS`). The meaning set and prompt set are
    checked against a given generation config while loading."""
    batch = BatchConfig.read(config).check_consistency()
    gen = None if generation_config is None else GenerationConfig.read(generation_config)
    if gen is None and batch.set != "demo":
        raise RunnerError(E_INPUTS, f"a {batch.set} batch needs its generation config")
    meaning_set = load_meanings(
        meanings, expected_sha256=None if gen is None else gen.meanings_sha256
    )
    fallback_set = load_fallback(fallback)
    prompt_set: PromptSet | None = None
    manifest: LlmManifest | None = None
    manifest_sha: str | None = None
    if proposers == "real":
        prompt_set = load_prompt_set(
            prompts if prompts is not None else default_prompt_set_dir(),
            meanings=meaning_set,
            expected=None if gen is None else gen.prompts,
        )
        manifest = load_llm_manifest(llm_manifest)
        manifest_sha = manifest_sha256(llm_manifest)
    if gen is None:
        gen = demo_generation_config(
            batch,
            meaning_set,
            fallback_set,
            prompt_set=prompt_set,
            llm_manifest_sha256=manifest_sha,
        )
    freeze: Mapping[str, Any] | None = None
    freeze_path: Path | None = None
    if freeze_manifest is not None:
        freeze_path = Path(freeze_manifest)
        data = read_json(freeze_path)
        if not isinstance(data, dict):
            raise RunnerError(E_INPUTS, f"{freeze_path}: a freeze manifest is a JSON object")
        freeze = data
    return BatchInputs(
        config=batch,
        generation_config=gen,
        meanings=meaning_set,
        fallback=fallback_set,
        prompt_set=prompt_set,
        llm_manifest=manifest,
        llm_manifest_sha256=manifest_sha,
        freeze_manifest=freeze,
        freeze_manifest_path=freeze_path,
    )


def check_batch_start(
    inputs: BatchInputs,
    *,
    kind: RunKind | str,
    run_id: str,
    proposers: ProposerMode = "real",
) -> None:
    """Every check a batch run passes before anything is created (#20).

    This is the one place for the start checks of every batch run, pilot and
    confirmatory runs included; `open_batch` and the `check` command call it and nothing
    else, and `Orchestrator` repeats steps 2 and 4 when it opens the run.

    1. The run ID fits the run kind (`rundir.check_run_id`: `DEMO-` exactly for demo and
       synthetic runs).
    2. The batch config, generation config, fallback set and meaning set pin the same
       threshold and hashes, and the batch's set fits the kind
       (`orchestrator.check_batch_pins`: `E_CONFIG`, `E_KIND`).
    3. Real proposers: the prompt set (#17) and its meaning set, the decoding schema
       (#16) and the LLM manifest (#16) hash to the generation config's values
       (`E_INPUTS`). Simulated proposers run demo and synthetic batches only (`E_MODE`).
    4. `genconfig.check_run_config`: the running code and constants equal the config,
       demo configs run demo/synthetic runs only, and a confirmatory run needs the G4
       freeze manifest with status `frozen` whose `config.frozen_sha256` equals the
       config hash (`ConfigMismatch`).

    G4 freeze guard (#25): add the comparison of the running files with the frozen
    manifest (`freeze.freeze_differences`, `inputs.freeze_manifest_path`) here, after
    step 4, so a confirmatory batch refuses to start on any frozen item that changed.
    """
    run_kind = RunKind(kind)
    gen = inputs.generation_config
    check_run_id(run_id, run_kind)
    check_batch_pins(inputs.config, gen, inputs.fallback, inputs.meanings, kind=run_kind)
    if proposers == "real":
        problems = []
        prompt_set = inputs.prompt_set
        if prompt_set is None:
            problems.append("A3 needs a prompt set")
        else:
            if prompt_set.hashes() != gen.prompts:
                problems.append("prompt-set hashes differ from the generation config")
            if prompt_set.meanings.sha256() != gen.meanings_sha256:
                problems.append("the prompt set quotes another meaning set")
        if decoding_schema_sha256() != gen.decoding_schema_sha256:
            problems.append("decoding-schema hash differs from the generation config")
        if inputs.llm_manifest is None or inputs.llm_manifest_sha256 is None:
            problems.append("A3 needs the LLM manifest")
        elif inputs.llm_manifest_sha256 != gen.llm_manifest_sha256:
            problems.append("LLM-manifest hash differs from the generation config")
        if problems:
            raise RunnerError(E_INPUTS, "; ".join(problems))
    elif run_kind not in PUBLIC_RUN_KINDS:
        raise RunnerError(E_MODE, "simulated proposers run demo and synthetic batches only")
    check_run_config(gen, kind=run_kind, freeze_manifest=inputs.freeze_manifest)


def probe_llm_server(
    url: str, manifest: LlmManifest, *, kind: RunKind | str, timeout_s: float = 5.0
) -> str:
    """Check the LLM server at `url` before the run starts; returns the runtime label of
    its `llm_request` records.

    `GET /v1/models` must list the manifest's model, and `GET /version` must report the
    manifest's runtime version (label `vllm <version>`) or, for demo and synthetic runs
    only, the mock server of #16 (label `mock_llm.MOCK_RUNTIME`). Anything else is
    `E_LLM_SERVER`."""
    run_kind = RunKind(kind)
    try:
        with httpx.Client(base_url=url.rstrip("/"), trust_env=False, timeout=timeout_s) as http:
            version_reply = http.get("/version")
            models_reply = http.get("/v1/models")
        version_reply.raise_for_status()
        models_reply.raise_for_status()
        version = version_reply.json().get("version")
        models = [m.get("id") for m in models_reply.json().get("data", [])]
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as err:
        raise RunnerError(E_LLM_SERVER, f"no usable LLM server at {url}: {err!r}") from err
    if manifest.model.id not in models:
        raise RunnerError(E_LLM_SERVER, f"{url} does not serve {manifest.model.id}")
    if version == manifest.runtime.version:
        return manifest.runtime_label()
    if version == f"{MOCK_NAME}-{MOCK_VERSION}":
        if run_kind not in PUBLIC_RUN_KINDS:
            raise RunnerError(
                E_LLM_SERVER, f"{url} is the mock server; a {run_kind} run needs vLLM"
            )
        return MOCK_RUNTIME
    raise RunnerError(
        E_LLM_SERVER,
        f"{url} reports runtime {version!r}; the LLM manifest pins {manifest.runtime_label()}",
    )


# ---------------------------------------------------------------------------
# Opening a batch run


@dataclass
class StudyBatch:
    """One opened batch run with its components (restricted unless DEMO)."""

    orchestrator: Orchestrator
    layout: RunLayout
    inputs: BatchInputs
    kind: RunKind
    clock: Clock
    store: VocabularyStore
    proposers: Mapping[Method, RoundProposer]
    resumed: bool
    ledger: SlotLedger | None = None
    """The shared slot ledger (real proposers)."""
    a1: A1SlotService | None = None
    """The A1 study service (real proposers); serve it with `a1.serve_a1`."""
    llm: OpenAICompatibleClient | None = None
    bot_designer: sim.BotDesigner | None = None
    """The bot designer of the last session (`run_session(designer="bot")`)."""


def _layout(run_dir: Path, kind: RunKind, *, resume: bool) -> RunLayout:
    run_id = run_dir.name
    if not resume:
        return create_run_dir(run_dir.parent, run_id, kind)
    check_run_id(run_id, kind)
    check_run_location(run_dir, kind)
    if not (run_dir / MANIFEST_NAME).is_file():
        raise RunnerError(E_RUN_DIR, f"{run_dir} is not a started run (no {MANIFEST_NAME})")
    return RunLayout(run_dir, run_id)


def open_batch(
    inputs: BatchInputs,
    run_dir: str | os.PathLike[str],
    *,
    kind: RunKind | str,
    clock: Clock,
    proposers: ProposerMode = "real",
    llm_url: str | None = None,
    a1_station: str | None = None,
    resume: bool = False,
    purpose: Literal["batch", "dry_run"] = "batch",
    sim_p_failure: float = 0.05,
    sim_propose: Mapping[Method, sim.ProposeFn] | None = None,
) -> StudyBatch:
    """Check the start rules, create (or, with `resume=True`, reopen) the run directory
    `run_dir` (its name is the run ID) and build the batch's components.

    Real proposers (`proposers="real"`): the LLM server at `llm_url` is checked first
    (`probe_llm_server`); then one `SlotLedger` on the run's slot log (refusals and
    timing to the run's logs) is shared by A1 (`a1.study_service`), A2 and A3 (the client
    logs every call to `llm-requests.jsonl`). Simulated proposers (`proposers="sim"`,
    demo/synthetic only) write the slot log directly (`_batch_sim.sim_proposers`).
    With a `ManualClock` the panel drives time, so slot events have no lead."""
    run_kind = RunKind(kind)
    root = Path(run_dir)
    check_batch_start(inputs, kind=run_kind, run_id=root.name, proposers=proposers)
    runtime: str | None = None
    if proposers == "real":
        if llm_url is None:
            raise RunnerError(E_LLM_SERVER, "real proposers need the LLM server URL")
        assert inputs.llm_manifest is not None
        runtime = probe_llm_server(llm_url, inputs.llm_manifest, kind=run_kind)
    layout = _layout(root, run_kind, resume=resume)
    config = inputs.config
    ledger: SlotLedger | None = None
    a1: A1SlotService | None = None
    client: OpenAICompatibleClient | None = None
    built: dict[Method, RoundProposer]
    if proposers == "real":
        assert llm_url is not None and runtime is not None
        assert inputs.llm_manifest is not None and inputs.prompt_set is not None
        ledger = SlotLedger(
            layout.log("slot"),
            run_id=layout.run_id,
            clock=clock,
            refusals=RecordWriter(layout.log("slot_refusal")),
            timing=RecordWriter(layout.log("timing")),
        )
        client = OpenAICompatibleClient(
            llm_url,
            inputs.llm_manifest.model.id,
            run_id=layout.run_id,
            clock=clock,
            request_log=RecordWriter(layout.log("llm_request")),
            runtime=runtime,
            model_revision=inputs.llm_manifest.model.revision,
        )
        a1 = study_service(
            layout, ledger, config, clock=clock, meanings=inputs.meanings, station=a1_station
        )
        built = {
            Method.A1: a1,
            Method.A2: A2Proposer(ledger, clock=clock),
            Method.A3: A3Proposer(
                client, ledger, inputs.prompt_set, decoding_schema(), clock=clock
            ),
        }
    else:
        built = dict(
            sim.sim_proposers(
                config,
                RecordWriter(layout.log("slot")),
                clock=clock,
                p_failure=sim_p_failure,
                propose=sim_propose,
            )
        )
    store = VocabularyStore(layout.store_dir, clock=clock.utc_now)
    orchestrator = Orchestrator(
        config,
        layout,
        built,
        store,
        inputs.fallback,
        clock=clock,
        generation_config=inputs.generation_config,
        meanings=inputs.meanings,
        kind=run_kind,
        freeze_manifest=inputs.freeze_manifest,
        purpose=purpose,
        slot_event_lead_ms=0 if isinstance(clock, ManualClock) else SLOT_EVENT_LEAD_MS,
    )
    return StudyBatch(
        orchestrator=orchestrator,
        layout=layout,
        inputs=inputs,
        kind=run_kind,
        clock=clock,
        store=store,
        proposers=built,
        resumed=resume,
        ledger=ledger,
        a1=a1,
        llm=client,
    )


# ---------------------------------------------------------------------------
# Serving and running a session


@contextmanager
def serve_panel(
    host: PanelSessionHost,
    *,
    clock: Clock,
    bind: str = "127.0.0.1",
    port: int = PANEL_PORT,
) -> Iterator[str]:
    """Serve the rater panel server (#21, `panel.create_panel_app`) for the session
    `host` (`Orchestrator.panel_host()`); yields its base URL. The stations open
    `<base>` + `rater_protocol.STATION_PAGE`. This is the one place the runner creates the
    panel app."""
    with serve_in_thread(create_panel_app(host, clock=clock), host=bind, port=port) as base:
        yield base


def wait_for_stations(
    orchestrator: Orchestrator, *, timeout_s: float = STATION_TIMEOUT_S, poll_s: float = 0.1
) -> None:
    """Block until every seat's station has joined the panel (real seconds); else
    `E_STATIONS` naming the missing stations."""
    deadline = time.monotonic() + timeout_s
    while True:
        missing = [station for station, up in orchestrator.console().stations if not up]
        if not missing:
            return
        if time.monotonic() >= deadline:
            raise RunnerError(E_STATIONS, f"stations not connected: {', '.join(missing)}")
        time.sleep(poll_s)


def _appointments(orchestrator: Orchestrator, appointment: Appointment) -> list[int]:
    nxt = orchestrator.next_atom()
    if nxt is None:
        return []
    first = orchestrator.config.atom_order.index(nxt) // ATOMS_PER_APPOINTMENT + 1
    if appointment == "all":
        return list(range(first, APPOINTMENTS_PER_BATCH + 1))
    if appointment == "next":
        return [first]
    return [int(appointment)]


def _stdout(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def run_session(
    batch: StudyBatch,
    *,
    appointment: Appointment = "next",
    panel: PanelMode = "stations",
    designer: DesignerMode = "kiosk",
    a1_host: str = "127.0.0.1",
    a1_port: int = A1_PORT,
    panel_host: str = "127.0.0.1",
    panel_port: int = PANEL_PORT,
    station_timeout_s: float = STATION_TIMEOUT_S,
    rating_policy: sim.RatingPolicy | None = None,
    designer_invalid_slots: frozenset[str] = frozenset(),
    designer_timeout_slots: frozenset[str] = frozenset(),
    on_panel: Callable[[str], None] | None = None,
    log: Callable[[str], None] = _stdout,
) -> str | None:
    """Serve the A1 page and the rater panel, then run the session's appointments.

    - A1 (real proposers): `a1.serve_a1(batch.a1, host=a1_host, port=a1_port)` for the
      designer's kiosk, or for a bot designer (`designer="bot"`, `_batch_sim.BotDesigner`).
    - Panel: `serve_panel` for the stations (`panel="stations"`; `on_panel(base_url)` is
      called once it serves, e.g. to start bot stations, and the session waits until
      every seat has joined), or in-process bot raters (`panel="bots"`,
      `_batch_sim.SyntheticPanel` with `rating_policy`, default the seeded bot policy).
    - A reopened run (`batch.resumed`) first finishes an interrupted atom
      (`Orchestrator.resume`).
    - `appointment`: `"next"` (the appointment of the next atom), `"all"` (the rest of
      the batch) or 1..4.

    Bots and bot designers run demo and synthetic batches only (`E_MODE`). Returns the next
    atom (`None` when the batch is finished); `BatchIncomplete` after a withdrawal."""
    orch = batch.orchestrator
    bots = panel == "bots" or (designer == "bot" and batch.a1 is not None)
    if bots and batch.kind not in PUBLIC_RUN_KINDS:
        raise RunnerError(E_MODE, f"bot raters and bot designers never run a {batch.kind} batch")
    if isinstance(batch.clock, ManualClock) and (
        panel != "bots" or (batch.a1 is not None and designer != "bot")
    ):
        raise RunnerError(E_MODE, "a manual clock needs bot raters and, with A1, a bot designer")
    run_id = batch.layout.run_id
    with ExitStack() as stack:
        if batch.a1 is not None:
            page = stack.enter_context(serve_a1(batch.a1, host=a1_host, port=a1_port))
            log(f"A1 designer page: {page}")
            if designer == "bot":
                batch.bot_designer = stack.enter_context(
                    sim.BotDesigner(
                        page,
                        run_id=run_id,
                        designer_id=batch.a1.designer_id,
                        invalid_slots=designer_invalid_slots,
                        timeout_slots=designer_timeout_slots,
                    )
                )
        host = orch.panel_host()
        if panel == "bots":
            policy = rating_policy if rating_policy is not None else sim.seeded_policy(run_id)
            stack.enter_context(sim.SyntheticPanel(host, batch.clock, policy))
        else:
            base = stack.enter_context(
                serve_panel(host, clock=batch.clock, bind=panel_host, port=panel_port)
            )
            log(f"Rater stations: {base}{STATION_PAGE}")
            if on_panel is not None:
                on_panel(base)
            log(f"Waiting for stations: {', '.join(s.station for s in host.seats())}")
            wait_for_stations(orch, timeout_s=station_timeout_s)
        if batch.resumed:
            orch.resume()
        for k in _appointments(orch, appointment):
            log(f"Appointment {k}: started")
            orch.run_appointment(k)
            log(f"Appointment {k}: done")
    nxt = orch.next_atom()
    view = orch.console()
    log(f"Atoms finished: {view.atoms_finished}/{len(orch.config.atom_order)}; next: {nxt}")
    return nxt


# ---------------------------------------------------------------------------
# Command line


_REFUSALS: Final = (RuntimeError, ValueError, OSError)
"""What `main` reports as a refusal (exit 1): `RunnerError`, `OrchestratorError`,
`genconfig.ConfigMismatch`, `llm_manifest.ModelMismatch` and a missing component
(`NotImplementedError`) are `RuntimeError`s; `rundir.RunPolicyError`,
`prompts.PromptSetError`, `FallbackError`, `RecordError` and `CodecError` are
`ValueError`s; unreadable files and busy ports are `OSError`s."""


def _input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run-dir", required=True, help="the run directory (its name is the run ID)"
    )
    parser.add_argument(
        "--kind", required=True, choices=[k.value for k in RunKind if k is not RunKind.PRACTICE]
    )
    parser.add_argument("--config", help="batch config (default for demo/synthetic: DEMO)")
    parser.add_argument("--generation-config", help="generation config (pilot/confirmatory)")
    parser.add_argument("--meanings", help="meaning-set directory (default for demo: DEMO)")
    parser.add_argument("--fallback", help="fallback manifest (default for demo: DEMO)")
    parser.add_argument("--prompts", help="prompt-set directory (default: generation/prompts)")
    parser.add_argument("--llm-manifest", help="LLM manifest (default: generation/llm)")
    parser.add_argument("--freeze-manifest", help="G4 freeze manifest (confirmatory runs)")
    parser.add_argument("--proposers", choices=("real", "sim"), default="real")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.batch_runner",
        description="Run a Study A batch with the real components (#20).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _input_args(sub.add_parser("check", help="run the start checks only"))
    run = sub.add_parser("run", help="serve A1 and the panel and run appointments")
    _input_args(run)
    run.add_argument("--llm-url", help="LLM server root, e.g. http://<llm-host>:8000")
    run.add_argument("--a1-host", default="127.0.0.1", help="interface of the A1 page")
    run.add_argument("--a1-port", type=int, default=A1_PORT)
    run.add_argument("--a1-station", help="station ID of the designer's kiosk")
    run.add_argument("--designer", choices=("kiosk", "bot"), default="kiosk")
    run.add_argument("--panel", choices=("stations", "bots"), default="stations")
    run.add_argument("--panel-host", default="127.0.0.1", help="interface of the panel")
    run.add_argument("--panel-port", type=int, default=PANEL_PORT)
    run.add_argument("--station-timeout-s", type=float, default=STATION_TIMEOUT_S)
    run.add_argument("--appointment", default="next", choices=("next", "all", "1", "2", "3", "4"))
    run.add_argument("--resume", action="store_true", help="reopen a started run")
    run.add_argument("--clock", choices=("real", "scaled", "manual"), default="real")
    run.add_argument("--speed", type=float, default=100.0, help="ScaledClock speed")
    run.add_argument("--purpose", choices=("batch", "dry_run"), default="batch")
    return parser


def _inputs_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> BatchInputs:
    public = RunKind(args.kind) in PUBLIC_RUN_KINDS
    defaults = {
        "config": examples_path("demo-batch-config.json"),
        "meanings": examples_path("demo-meanings"),
        "fallback": sound_root() / sim.DEMO_FALLBACK_MANIFEST,
    }
    paths: dict[str, Any] = {}
    for name, default in defaults.items():
        value = getattr(args, name)
        if value is None and not public:
            parser.error(f"--{name} is required for {args.kind} runs")
        paths[name] = value if value is not None else default
    return load_batch_inputs(
        config=paths["config"],
        meanings=paths["meanings"],
        fallback=paths["fallback"],
        generation_config=args.generation_config,
        prompts=args.prompts,
        llm_manifest=args.llm_manifest,
        freeze_manifest=args.freeze_manifest,
        proposers=args.proposers,
    )


def _clock(args: argparse.Namespace) -> Clock:
    if args.clock != "real" and RunKind(args.kind) not in PUBLIC_RUN_KINDS:
        raise RunnerError(E_MODE, f"a {args.kind} run uses the real clock")
    if args.clock == "scaled":
        return ScaledClock(args.speed)
    if args.clock == "manual":
        return ManualClock()
    return SystemClock()


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    inputs = _inputs_from_args(args, parser)
    run_dir = Path(args.run_dir)
    if args.command == "check":
        check_batch_start(inputs, kind=args.kind, run_id=run_dir.name, proposers=args.proposers)
        _stdout(
            f"ok: run {run_dir.name} ({args.kind}), batch {inputs.config.batch_id}, "
            f"generation config {inputs.generation_config.frozen_sha256()}"
        )
        return 0
    batch = open_batch(
        inputs,
        run_dir,
        kind=args.kind,
        clock=_clock(args),
        proposers=args.proposers,
        llm_url=args.llm_url,
        a1_station=args.a1_station,
        resume=args.resume,
        purpose=args.purpose,
    )
    _stdout(f"Run {batch.layout.run_id} ({batch.kind}): batch {inputs.config.batch_id}")
    appointment: Appointment = (
        args.appointment if args.appointment in ("next", "all") else int(args.appointment)
    )
    run_session(
        batch,
        appointment=appointment,
        panel=args.panel,
        designer=args.designer,
        a1_host=args.a1_host,
        a1_port=args.a1_port,
        panel_host=args.panel_host,
        panel_port=args.panel_port,
        station_timeout_s=args.station_timeout_s,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m av_generation.batch_runner check|run ...` (exit 0 ok, 1 refused, 3 batch
    incomplete, 130 interrupted)."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _run(args, parser)
    except BatchIncomplete as err:
        sys.stderr.write(f"batch incomplete: {err}\n")
        return 3
    except _REFUSALS as err:
        sys.stderr.write(f"error: {err}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("interrupted; the logs are kept: reopen the run with --resume\n")
        return 130


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())

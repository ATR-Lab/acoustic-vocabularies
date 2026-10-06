"""Launcher for the pinned vLLM server (#16; Study A protocol §3.6, Study B protocol §4).

The server starts from the pinned config file `generation/llm/server-config.json`
(`ServerConfig`, schema `generation/llm/server-config.schema.json`) and the LLM manifest
(`llm_manifest`). Before anything is started, `prepare_launch` checks, in order:

1. the manifest is valid and agrees with the code (`E_MANIFEST`);
2. the config is valid (`E_CONFIG`) and names the manifest by its file SHA-256
   (`E_CONFIG_MANIFEST`), and its model name, precision, `max_model_len`, generation
   config, structured-outputs backend, engine seed and load format equal the manifest's
   (`E_CONFIG`);
3. the installed vLLM is the pinned version (`E_RUNTIME_MISSING`, `E_RUNTIME_VERSION`);
4. the model directory matches the manifest: every file present with the pinned size,
   revision evidence equal to the pinned revision, git blob of the small files, chat
   template hash, no stray weight files, and the SHA-256 of every weights shard
   (`llm_manifest.verify_model_dir`; `E_MISSING`, `E_SIZE`, `E_REVISION`,
   `E_REVISION_UNKNOWN`, `E_FILE_BLOB`, `E_CHAT_TEMPLATE`, `E_EXTRA_WEIGHTS`,
   `E_WEIGHTS_SHA256`).

Any failure raises `ServerRefused` with a clear message and starts nothing. Then
`start_server` runs `vllm serve` with the pinned flags and an offline environment
(`OFFLINE_ENV`: no Hugging Face Hub access, no telemetry, no vLLM usage statistics) and
waits for `GET /health`. Startup is timed apart from slots: `startup_start` and
`startup_end` timing events (`component="llm"`, verify and load times in `detail`).

CLI (LLM host):

    python -m av_generation.llm_server check --model-dir DIR
    python -m av_generation.llm_server serve --model-dir DIR --host <station-net address> \\
        [--timing-log PATH --run-id ID]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Final, Literal, Self

import httpx

from av_generation.clock import Clock, SystemClock, utc_text
from av_generation.jsonio import CodecError, decode_dataclass, read_json, to_json_value
from av_generation.llm_manifest import (
    LlmManifest,
    ModelMismatch,
    llm_dir,
    llm_schema_errors,
    load_llm_manifest,
    manifest_path,
    manifest_sha256,
    verify_model_dir,
)
from av_generation.records import RecordWriter, TimingEvent

SERVER_CONFIG_FORMAT: Final = "av-generation/llm-server-config"
SERVER_CONFIG_VERSION: Final = 1
HEALTH_PATH: Final = "/health"
COMPONENT: Final = "llm"

OFFLINE_ENV: Final[Mapping[str, str]] = {
    "HF_HUB_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "VLLM_NO_USAGE_STATS": "1",
    "VLLM_DO_NOT_TRACK": "1",
    "DO_NOT_TRACK": "1",
}
"""Environment of the server process: nothing may leave the station network."""

E_CONFIG: Final = "E_CONFIG"
E_CONFIG_MANIFEST: Final = "E_CONFIG_MANIFEST"
E_RUNTIME_MISSING: Final = "E_RUNTIME_MISSING"
E_RUNTIME_VERSION: Final = "E_RUNTIME_VERSION"
E_STARTUP: Final = "E_STARTUP"


class ServerRefused(RuntimeError):
    """The server may not start; `.code` names the first failed rule, `.problems` all."""

    def __init__(self, code: str, problems: Sequence[str]) -> None:
        self.code = code
        self.problems = tuple(problems)
        lines = "\n  - ".join(self.problems)
        super().__init__(f"{code}: LLM server refused to start:\n  - {lines}")


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """`generation/llm/server-config.json` (see the module docstring)."""

    name: str
    manifest_sha256: str
    served_model_name: str
    host: str
    port: int
    dtype: str
    max_model_len: int
    generation_config: Literal["vllm"]
    structured_outputs_backend: str
    engine_seed: int
    load_format: str
    gpu_memory_utilization: float
    tensor_parallel_size: int
    startup_timeout_s: int

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        data["format"] = SERVER_CONFIG_FORMAT
        data["format_version"] = SERVER_CONFIG_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ServerConfig:
        errors = llm_schema_errors("server-config.schema.json", dict(data))
        if errors:
            raise ServerRefused(E_CONFIG, errors)
        body = {k: v for k, v in data.items() if k not in ("format", "format_version")}
        try:
            return decode_dataclass(cls, body)
        except CodecError as err:  # pragma: no cover - the schema catches it first
            raise ServerRefused(E_CONFIG, [str(err)]) from err


def server_config_path() -> Path:
    """The committed `generation/llm/server-config.json`."""
    return llm_dir() / "server-config.json"


def load_server_config(path: str | os.PathLike[str] | None = None) -> ServerConfig:
    try:
        data = read_json(path if path is not None else server_config_path())
    except CodecError as err:
        raise ServerRefused(E_CONFIG, [str(err)]) from err
    if not isinstance(data, dict):
        raise ServerRefused(E_CONFIG, ["the server config must be a JSON object"])
    return ServerConfig.from_dict(data)


def config_problems(
    config: ServerConfig, manifest: LlmManifest, *, manifest_file_sha256: str
) -> list[tuple[str, str]]:
    """Disagreements between the server config and the manifest (`(code, text)`)."""
    problems = []
    if config.manifest_sha256 != manifest_file_sha256:
        problems.append(
            (
                E_CONFIG_MANIFEST,
                f"config names manifest {config.manifest_sha256}, file is {manifest_file_sha256}",
            )
        )
    rt = manifest.runtime
    pairs = (
        ("served_model_name", config.served_model_name, manifest.model.id),
        ("dtype", config.dtype, rt.precision),
        ("max_model_len", config.max_model_len, rt.max_model_len),
        ("generation_config", config.generation_config, rt.generation_config),
        (
            "structured_outputs_backend",
            config.structured_outputs_backend,
            rt.structured_outputs_backend,
        ),
        ("engine_seed", config.engine_seed, rt.engine_seed),
        ("load_format", config.load_format, rt.load_format),
    )
    for name, found, pinned in pairs:
        if found != pinned:
            problems.append((E_CONFIG, f"{name} {found!r} differs from the manifest {pinned!r}"))
    return problems


def installed_vllm_version() -> str | None:
    """The vLLM version installed in this environment (`None` if absent)."""
    try:
        return metadata.version("vllm")
    except metadata.PackageNotFoundError:
        return None


def vllm_command(
    config: ServerConfig,
    model_dir: str | os.PathLike[str],
    *,
    executable: Sequence[str] = ("vllm",),
    host: str | None = None,
    port: int | None = None,
) -> list[str]:
    """`vllm serve` with the pinned flags (host and port may be set per LLM host)."""
    backend = json.dumps({"backend": config.structured_outputs_backend}, separators=(",", ":"))
    return [
        *executable,
        "serve",
        str(model_dir),
        "--served-model-name",
        config.served_model_name,
        "--dtype",
        config.dtype,
        "--max-model-len",
        str(config.max_model_len),
        "--generation-config",
        config.generation_config,
        "--structured-outputs-config",
        backend,
        "--seed",
        str(config.engine_seed),
        "--load-format",
        config.load_format,
        "--gpu-memory-utilization",
        repr(config.gpu_memory_utilization),
        "--tensor-parallel-size",
        str(config.tensor_parallel_size),
        "--host",
        host if host is not None else config.host,
        "--port",
        str(port if port is not None else config.port),
    ]


def server_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """`base` (default: this process's environment) plus `OFFLINE_ENV`."""
    env = dict(os.environ if base is None else base)
    env.update(OFFLINE_ENV)
    return env


def client_host(host: str) -> str:
    """Address a local client uses for a bind address (`0.0.0.0` -> loopback)."""
    return {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1"}.get(host, host)


def base_url_for(host: str, port: int) -> str:
    h = client_host(host)
    return f"http://[{h}]:{port}" if ":" in h else f"http://{h}:{port}"


# ---------------------------------------------------------------------------
# Startup timing


@dataclass
class StartupLog:
    """Writes the `startup_start` / `startup_end` timing events of the LLM server."""

    clock: Clock
    writer: RecordWriter | None = None
    run_id: str | None = None
    started_ms: int | None = None
    lines: list[str] = field(default_factory=list)

    def _event(self, event: str, *, duration_ms: int | None, detail: str) -> None:
        self.lines.append(f"{event} t_ms={self.clock.now_ms()} {detail}")
        if self.writer is None or self.run_id is None:
            return
        self.writer.append(
            TimingEvent(
                run_id=self.run_id,
                event=event,
                t_ms=self.clock.now_ms(),
                wall_utc=utc_text(self.clock.utc_now()),
                component=COMPONENT,
                duration_ms=duration_ms,
                detail=detail[:400],
            )
        )

    def start(self, detail: str) -> None:
        self.started_ms = self.clock.now_ms()
        self._event("startup_start", duration_ms=None, detail=detail)

    def elapsed_ms(self) -> int:
        return 0 if self.started_ms is None else self.clock.now_ms() - self.started_ms

    def end(self, detail: str) -> None:
        self._event("startup_end", duration_ms=self.elapsed_ms(), detail=detail)


# ---------------------------------------------------------------------------
# Launch


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """A checked launch: the command, environment and where the server will answer."""

    command: tuple[str, ...]
    env: Mapping[str, str]
    base_url: str
    config: ServerConfig
    manifest: LlmManifest
    manifest_sha256: str
    verify_ms: int
    startup: StartupLog


def prepare_launch(
    model_dir: str | os.PathLike[str],
    *,
    config_path: str | os.PathLike[str] | None = None,
    manifest_file: str | os.PathLike[str] | None = None,
    host: str | None = None,
    port: int | None = None,
    executable: Sequence[str] = ("vllm",),
    runtime_version: Callable[[], str | None] = installed_vllm_version,
    clock: Clock | None = None,
    timing: RecordWriter | None = None,
    run_id: str | None = None,
    progress: Callable[[str], None] | None = None,
    env: Mapping[str, str] | None = None,
) -> LaunchPlan:
    """Run every check of the module docstring; raises `ServerRefused` (nothing started)."""
    clock = clock if clock is not None else SystemClock()
    startup = StartupLog(clock, timing, run_id)
    startup.start(f"llm-server verify model_dir={Path(model_dir).name}")
    try:
        path = Path(manifest_file) if manifest_file is not None else manifest_path()
        manifest = load_llm_manifest(path)
        digest = manifest_sha256(path)
        config = load_server_config(config_path)
        problems = config_problems(config, manifest, manifest_file_sha256=digest)
        if problems:
            raise ServerRefused(problems[0][0], [p for _, p in problems])
        installed = runtime_version()
        if installed is None:
            raise ServerRefused(
                E_RUNTIME_MISSING, [f"vLLM is not installed; pinned {manifest.runtime.version}"]
            )
        if installed != manifest.runtime.version:
            raise ServerRefused(
                E_RUNTIME_VERSION,
                [f"installed vLLM {installed} differs from the pinned {manifest.runtime.version}"],
            )
        verify_model_dir(manifest, model_dir, progress=progress)
    except ModelMismatch as err:
        startup.end(f"refused {err.code}")
        raise ServerRefused(err.code, err.problems) from err
    except ServerRefused as err:
        startup.end(f"refused {err.code}")
        raise
    bind_host = host if host is not None else config.host
    bind_port = port if port is not None else config.port
    return LaunchPlan(
        command=tuple(
            vllm_command(config, model_dir, executable=executable, host=bind_host, port=bind_port)
        ),
        env=server_env(env),
        base_url=base_url_for(bind_host, bind_port),
        config=config,
        manifest=manifest,
        manifest_sha256=digest,
        verify_ms=startup.elapsed_ms(),
        startup=startup,
    )


class ServerProcess:
    """A running server started by `start_server` (context manager; `stop()` on exit)."""

    def __init__(self, process: subprocess.Popen[bytes], plan: LaunchPlan, startup_ms: int) -> None:
        self.process = process
        self.plan = plan
        self.base_url = plan.base_url
        self.startup_ms = startup_ms

    def stop(self, timeout_s: float = 30.0) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn server
                self.process.kill()
                self.process.wait(timeout=timeout_s)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()


def health_ok(base_url: str) -> bool:
    """`GET /health` answers 200."""
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            return client.get(base_url + HEALTH_PATH).status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def wait_until_ready(
    base_url: str,
    *,
    clock: Clock,
    timeout_s: float,
    process: subprocess.Popen[bytes] | None = None,
    poll_s: float = 0.25,
) -> int:
    """Poll `/health` until it answers; returns the clock milliseconds waited. Raises
    `ServerRefused(E_STARTUP)` when the process exits or the timeout passes."""
    start = clock.now_ms()
    while True:
        if health_ok(base_url):
            return clock.now_ms() - start
        if process is not None and process.poll() is not None:
            raise ServerRefused(E_STARTUP, [f"server exited with code {process.returncode}"])
        if clock.now_ms() - start > timeout_s * 1000:
            raise ServerRefused(E_STARTUP, [f"server not ready after {timeout_s} s"])
        clock.sleep(poll_s)


def start_server(
    plan: LaunchPlan,
    *,
    startup_timeout_s: float | None = None,
    log_file: IO[bytes] | None = None,
) -> ServerProcess:
    """Start the checked server and wait until it answers `/health`."""
    clock = plan.startup.clock
    timeout_s = (
        startup_timeout_s if startup_timeout_s is not None else plan.config.startup_timeout_s
    )
    process = subprocess.Popen(  # noqa: S603 - the command is built from the pinned config
        list(plan.command),
        env=dict(plan.env),
        stdout=log_file,
        stderr=subprocess.STDOUT if log_file is not None else None,
    )
    try:
        load_ms = wait_until_ready(plan.base_url, clock=clock, timeout_s=timeout_s, process=process)
    except ServerRefused as err:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        plan.startup.end(f"failed {err.code}")
        raise
    startup_ms = plan.startup.elapsed_ms()
    plan.startup.end(
        f"ready verify_ms={plan.verify_ms} load_ms={load_ms} manifest_sha256={plan.manifest_sha256}"
    )
    return ServerProcess(process, plan, startup_ms)


# ---------------------------------------------------------------------------
# CLI


def _plan_from_args(args: argparse.Namespace) -> LaunchPlan:
    if args.timing_log and not args.run_id:
        raise ServerRefused(E_CONFIG, ["--timing-log needs --run-id"])
    timing = RecordWriter(args.timing_log, types=(TimingEvent,)) if args.timing_log else None
    return prepare_launch(
        args.model_dir,
        config_path=args.config,
        manifest_file=args.manifest,
        host=args.host,
        port=args.port,
        executable=(args.vllm,),
        runtime_version=lambda: installed_vllm_version(),
        timing=timing,
        run_id=args.run_id,
        progress=lambda message: print(message, flush=True),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m av_generation.llm_server")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("check", "run every check and print the server command; start nothing"),
        ("serve", "check, then start the server and wait for it"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--model-dir", required=True)
        p.add_argument("--config", default=None, help="default: generation/llm/server-config.json")
        p.add_argument("--manifest", default=None, help="default: generation/llm/manifest.json")
        p.add_argument("--host", default=None)
        p.add_argument("--port", type=int, default=None)
        p.add_argument("--vllm", default="vllm", help="vLLM executable")
        p.add_argument("--timing-log", default=None, help="timing JSONL (startup events)")
        p.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    try:
        plan = _plan_from_args(args)
    except ServerRefused as err:
        print(f"REFUSED {err}", file=sys.stderr)
        return 2
    if plan.manifest.hardware.status == "pending":
        print(
            "note: hardware not recorded in the manifest; run "
            "`python -m av_generation.llm_manifest record-hardware` on this host",
            file=sys.stderr,
        )
    print("command: " + " ".join(plan.command))
    if args.command == "check":
        plan.startup.end("checked; not started")
        print(f"OK verify_ms={plan.verify_ms}")
        return 0
    try:  # pragma: no cover - needs the LLM host (exercised with the mock executable)
        server = start_server(plan)
    except ServerRefused as err:  # pragma: no cover
        print(f"FAILED {err}", file=sys.stderr)
        return 3
    print(f"READY {server.base_url} startup_ms={server.startup_ms}", flush=True)  # pragma: no cover
    try:  # pragma: no cover
        return server.process.wait()
    except KeyboardInterrupt:  # pragma: no cover
        server.stop()
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

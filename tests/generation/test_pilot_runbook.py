"""Keep the pilot-day runbook honest (#311).

Every `python -m ...` command in the code blocks of `generation/docs/pilot-day-runbook.md`
must name a module of the `--project` it runs in, with a `main`, and its arguments
must parse with that module's own argparse parser (placeholders `<...>` replaced by
temporary paths). The parser is stopped right after parsing, so nothing runs. A later
change to a command line (a renamed module, subcommand or flag, a new required flag)
then fails here instead of on the pilot day.
"""

import argparse
import importlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNBOOK = REPO / "generation" / "docs" / "pilot-day-runbook.md"
PROJECTS = {"generation": "av_generation", "schedules": "av_schedules"}
"""`uv run --project <dir>` -> the import package the commands of that project use."""
MAIN_MODULES = {"av_schedules": "av_schedules.cli"}
"""Packages whose `__main__` calls the `main` of another module."""
NUMERIC_PLACEHOLDERS = {"<gain-db>": "-12.0"}
"""Placeholders of numeric options (typed values, so a path would not parse)."""
SHELL_STOP = {"|", "||", "&&", ";", "&", ">", ">>", "2>", "2>&1"}


@dataclass(frozen=True)
class Command:
    line: int
    project: str
    module: str
    argv: tuple[str, ...]

    @property
    def label(self) -> str:
        sub = next((a for a in self.argv if not a.startswith("-")), "")
        return f"L{self.line}:{self.module.rsplit('.', 1)[-1]}:{sub}"


def _code_blocks(text: str) -> list[tuple[int, list[str]]]:
    """(first line number, lines) of every ```bash / ```sh block."""
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] | None = None
    start = 0
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if current is None and stripped in ("```bash", "```sh"):
            current, start = [], number + 1
        elif current is not None and stripped == "```":
            blocks.append((start, current))
            current = None
        elif current is not None:
            current.append(line)
    assert current is None, "unclosed code block"
    return blocks


def _logical_lines(start: int, lines: list[str]) -> list[tuple[int, str]]:
    """Join `\\` continuations; keep the first line number of each command."""
    out: list[tuple[int, str]] = []
    buffer, first = "", start
    for offset, line in enumerate(lines):
        if not buffer:
            first = start + offset
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
            continue
        out.append((first, buffer + line))
        buffer = ""
    assert not buffer, f"line {first}: a continuation ends the block"
    return out


def runbook_commands(text: str) -> list[Command]:
    commands = []
    for start, lines in _code_blocks(text):
        for number, line in _logical_lines(start, lines):
            if "python -m " not in line or line.lstrip().startswith("#"):
                continue
            tokens = shlex.split(line, comments=True)
            assert tokens[:4] == ["uv", "run", "--project", tokens[3]], (
                f"line {number}: commands run as `uv run --project <package> python -m ...`"
            )
            m = tokens.index("-m")
            assert tokens[m - 1] == "python", f"line {number}: `python -m` expected"
            argv: list[str] = []
            for token in tokens[m + 2 :]:
                if token in SHELL_STOP:
                    break
                argv.append(token)
            commands.append(Command(number, tokens[3], tokens[m + 1], tuple(argv)))
    return commands


COMMANDS = runbook_commands(RUNBOOK.read_text(encoding="utf-8"))


class _Parsed(BaseException):
    """Raised right after the outermost parser call returns (nothing else runs)."""

    def __init__(self, namespace: argparse.Namespace) -> None:
        super().__init__()
        self.namespace = namespace


@pytest.fixture
def stop_after_parsing(monkeypatch):
    """Patch argparse so the outermost `parse_args` / `parse_known_args` raises
    `_Parsed` with its result instead of returning; inner calls (subparsers, and
    `parse_known_args` inside `parse_args`) behave as usual."""
    depth = 0
    parse_args = argparse.ArgumentParser.parse_args
    parse_known_args = argparse.ArgumentParser.parse_known_args

    def wrap(original, known):
        def patched(self, args=None, namespace=None):
            nonlocal depth
            depth += 1
            try:
                result = original(self, args, namespace)
            finally:
                depth -= 1
            if depth == 0:
                raise _Parsed(result[0] if known else result)
            return result

        return patched

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", wrap(parse_args, False))
    monkeypatch.setattr(argparse.ArgumentParser, "parse_known_args", wrap(parse_known_args, True))


def _substitute(argv: tuple[str, ...], tmp_path: Path) -> list[str]:
    def placeholder(match: re.Match[str]) -> str:
        text = match[0]
        if text in NUMERIC_PLACEHOLDERS:
            return NUMERIC_PLACEHOLDERS[text]
        return str(tmp_path / re.sub(r"[^A-Za-z0-9._-]", "_", text.strip("<>")))

    return [re.sub(r"<[^<>]+>", placeholder, arg) for arg in argv]


def test_the_runbook_has_the_commands_of_the_day():
    modules = {c.module for c in COMMANDS}
    for needed in (
        "av_generation.batch_setup",
        "av_generation.batch_runner",
        "av_generation.llm_server",
        "av_generation.audit",
        "av_generation.threshold_cli",
        "av_generation.dryrun",
        "av_generation.freeze",
    ):
        assert needed in modules, needed
    assert len(COMMANDS) >= 40


def test_rehearsal_commands_write_only_to_ignored_folders():
    """A command without `<...>` placeholders is a rehearsal: everything it writes goes
    under `generation/out/` (git-ignored), and every run, set, batch and session it names
    is DEMO-."""
    out_flags = {"--out", "--out-dir", "--run-dir", "--runs-root", "--evidence", "--manifest"}
    out_flags |= {"--masked-out", "--unmasked-out"}
    checked = 0
    for command in COMMANDS:
        if any("<" in a for a in command.argv):
            continue
        checked += 1
        for flag, value in zip(command.argv, command.argv[1:], strict=False):
            if flag in out_flags:
                assert value.startswith("generation/out/"), f"line {command.line}: {value}"
            if flag in ("--run-id", "--set-id", "--name", "--demo-set-ns", "--demo-seed"):
                assert value.startswith("DEMO-"), f"line {command.line}: {flag} {value}"
            if flag == "--kind":
                assert value == "demo", f"line {command.line}: --kind {value}"
    assert checked >= 20


def test_real_commands_write_to_restricted_storage():
    """A command for a real session (it names `<restricted>`) never writes into the
    repository: every output option is a placeholder path."""
    out_flags = {"--out", "--out-dir", "--run-dir", "--runs-root", "--masked-out"}
    out_flags |= {"--unmasked-out", "--timing-log", "--manifest"}
    for command in COMMANDS:
        if not any("<restricted>" in a for a in command.argv):
            continue
        for flag, value in zip(command.argv, command.argv[1:], strict=False):
            if flag in out_flags:
                assert value.startswith("<"), f"line {command.line}: {flag} {value}"
        assert "--kind" not in command.argv or "pilot" in command.argv, command.line


@pytest.mark.parametrize("command", COMMANDS, ids=[c.label for c in COMMANDS])
def test_command_parses(command, tmp_path, monkeypatch, stop_after_parsing, capsys):
    package = PROJECTS.get(command.project)
    assert package is not None, f"line {command.line}: unknown project {command.project}"
    assert (REPO / command.project / "pyproject.toml").is_file()
    assert command.module == package or command.module.startswith(f"{package}."), (
        f"line {command.line}: {command.module} is not in the {command.project} project"
    )
    if package != "av_generation":  # standard-library packages: import from the tree
        monkeypatch.syspath_prepend(str(REPO / command.project / "src"))
    module = importlib.import_module(MAIN_MODULES.get(command.module, command.module))
    main = getattr(module, "main", None)
    assert callable(main), f"line {command.line}: {command.module} has no main()"
    argv = _substitute(command.argv, tmp_path)
    try:
        main(argv)
    except _Parsed as parsed:
        assert isinstance(parsed.namespace, argparse.Namespace)
    except SystemExit as exit_:
        pytest.fail(
            f"line {command.line}: `{command.module} {' '.join(argv)}` does not parse "
            f"(exit {exit_.code}): {capsys.readouterr().err.strip()}"
        )
    else:  # pragma: no cover - only when a main skips argparse
        pytest.fail(f"line {command.line}: {command.module} ran without parsing arguments")


def test_extraction_handles_continuations_comments_and_pipes():
    text = "\n".join(
        [
            "```bash",
            "# a comment with python -m nothing",
            "uv run --project generation python -m av_generation.audit batch \\",
            "    <run> --strict  # trailing comment",
            "uv run --project generation python -m av_generation.audit tally x --out y | tee z",
            "curl -s http://<llm-host>:8000/health",
            "```",
            "```text",
            "uv run --project generation python -m av_generation.never parsed",
            "```",
        ]
    )
    commands = runbook_commands(text)
    assert [(c.line, c.module, c.argv) for c in commands] == [
        (3, "av_generation.audit", ("batch", "<run>", "--strict")),
        (5, "av_generation.audit", ("tally", "x", "--out", "y")),
    ]

"""Command line: shared commands work, issue commands are registered by their modules."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from av_analysis import __version__, cli
from av_analysis.cli import (
    ISSUE_COMMANDS,
    REFRESH_STEPS,
    build_parser,
    main,
    templates_dir_from_env,
)
from av_analysis.paths import MARKER
from av_analysis.templates import TEMPLATES


def test_every_command_is_registered():
    parser, handlers = build_parser()
    assert set(handlers) == {"init-root", "schemas", "check-templates", "refresh", *ISSUE_COMMANDS}
    assert {owner for _, owner, _ in ISSUE_COMMANDS.values()} == {"#33", "#34", "#35"}
    help_text = parser.format_help()
    for name in handlers:
        assert name in help_text


def test_version_and_module_entry_point(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"av-analysis {__version__}"
    result = subprocess.run(
        [sys.executable, "-m", "av_analysis", "schemas"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_init_root(tmp_path, capsys):
    target = tmp_path / "root"
    assert main(["init-root", str(target), "--kind", "SYNTHETIC", "--label", "DEMO-cli"]) == 0
    assert json.loads((target / MARKER).read_text(encoding="utf-8"))["data_kind"] == "SYNTHETIC"
    assert "SYNTHETIC data root" in capsys.readouterr().out
    assert main(["init-root", str(target), "--kind", "SYNTHETIC", "--label", "plain"]) == 2
    assert "refusing" in capsys.readouterr().err


def test_schemas_check_passes():
    assert main(["schemas"]) == 0


def test_check_templates(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AV_TEMPLATES_DIR", raising=False)
    monkeypatch.delenv("AV_PLANNING_DIR", raising=False)
    assert templates_dir_from_env() is None
    assert main(["check-templates"]) == 2
    for t in TEMPLATES.values():
        (tmp_path / t.filename).write_bytes((t.header + "\r\n").encode())
    monkeypatch.setenv("AV_TEMPLATES_DIR", str(tmp_path))
    assert main(["check-templates"]) == 0
    (tmp_path / TEMPLATES["trial-log"].filename).write_bytes(b"study\r\n")
    assert main(["check-templates", str(tmp_path)]) == 1
    assert "problem:" in capsys.readouterr().err


def test_templates_dir_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AV_TEMPLATES_DIR", raising=False)
    monkeypatch.setenv("AV_PLANNING_DIR", str(tmp_path / "planning-materials"))
    assert templates_dir_from_env() == tmp_path / "templates"
    monkeypatch.setenv("AV_TEMPLATES_DIR", str(tmp_path / "t"))
    assert templates_dir_from_env() == tmp_path / "t"


@pytest.mark.parametrize("name", sorted(ISSUE_COMMANDS))
def test_issue_commands_parse_their_arguments(name):
    parser, _ = build_parser()
    required = {
        "synth-logs": ["--demo-seed", "DEMO-x", "--out", "o"],
        "reconcile": ["--root", "r", "A-C01-L01-D0"],
        "derive": ["--root", "r"],
        "run": ["--study", "A", "--data", "d"],
        "simulate": ["--scenario", "null-A", "--seed", "DEMO-x", "--out", "o"],
        "dashboard": ["--root", "r"],
    }
    args = parser.parse_args([name, *required[name]])
    assert args.command == name


def test_refresh_runs_reconcile_derive_dashboard_and_skips_missing_steps(
    tmp_path, monkeypatch, capsys
):
    assert [step for step, _ in REFRESH_STEPS] == ["reconcile", "derive", "dashboard"]
    # In the skeleton every step is an interface: all are skipped.
    assert main(["refresh", "--root", str(tmp_path)]) == 3
    assert capsys.readouterr().err.count("skipped") == 3

    calls = []
    real_build_parser = cli.build_parser

    def with_steps(codes):
        """build_parser whose refresh steps are fakes returning the given exit codes."""

        def build():
            parser, handlers = real_build_parser()
            for step, code in codes.items():

                def handler(args, step=step, code=code):
                    calls.append((step, args.root, getattr(args, "all", None)))
                    return code

                handlers[step] = handler
            return parser, handlers

        return build

    monkeypatch.setattr(cli, "build_parser", with_steps({"reconcile": 1, "derive": 0}))
    assert cli.main(["refresh", "--root", "r"]) == 1  # findings: continue, dashboard skipped
    assert calls == [("reconcile", "r", True), ("derive", "r", None)]
    assert "refresh: reconcile exit 1" in capsys.readouterr().out

    calls.clear()
    steps = {"reconcile": 2, "derive": 0, "dashboard": 0}
    monkeypatch.setattr(cli, "build_parser", with_steps(steps))
    assert cli.main(["refresh", "--root", "r"]) == 2  # refused input stops the sequence
    assert calls == [("reconcile", "r", True)]

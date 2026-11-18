"""Requirement trace: station configuration for the selection menus (public schema and DEMO example only)."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT/'apparatus/schemas/station.schema.json'
EXAMPLE = ROOT/'apparatus/examples/station.example.json'


@pytest.mark.xfail(strict=True, reason='Requirement gap: the station configuration has no visible menu countdown flag')
def test_station_has_a_menu_countdown_flag_that_is_off_by_default():
    """REQ-MENU-08: a boolean station flag controls a visible menu countdown and it is off unless set."""
    schema = json.loads(SCHEMA.read_text(encoding='utf-8'))
    flags = {name: spec for name, spec in schema['properties'].items() if 'countdown' in name.lower()}
    assert len(flags) == 1
    (name, spec), = flags.items()
    assert spec.get('type') == 'boolean'
    example = json.loads(EXAMPLE.read_text(encoding='utf-8'))
    if name in schema.get('required', []):
        assert example[name] is False
    else:
        assert spec.get('default', False) is False and example.get(name, False) is False

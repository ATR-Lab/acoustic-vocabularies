import importlib.util
from pathlib import Path


def test_registered_schema_fixtures():
    path = Path(__file__).resolve().parents[1] / "tools/validate_schemas.py"
    spec = importlib.util.spec_from_file_location("validate_schemas", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schemas, examples = module.validate()
    assert schemas >= 1 and examples >= 1

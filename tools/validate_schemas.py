"""Validate Draft 2020-12 schemas and explicitly registered public fixtures."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def validate() -> tuple[int, int]:
    schemas = list(ROOT.glob("**/*.schema.json"))
    schemas = [p for p in schemas if not any(x in {".venv", ".local", "Library", "node_modules"} for x in p.parts)]
    for path in schemas:
        schema = json.loads(path.read_text(encoding="utf-8-sig"))
        Draft202012Validator.check_schema(schema)
    manifest = json.loads((ROOT / "tests/schema-fixtures.json").read_text(encoding="utf-8"))
    for pair in manifest:
        schema = json.loads((ROOT / pair["schema"]).read_text(encoding="utf-8-sig"))
        instance = json.loads((ROOT / pair["instance"]).read_text(encoding="utf-8-sig"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(instance)
    return len(schemas), len(manifest)


if __name__ == "__main__":
    count, examples = validate()
    print(f"PASS: {count} schemas and {examples} synthetic examples")

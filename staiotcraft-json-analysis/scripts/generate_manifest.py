import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "inferred_ai_json_schemas"
JSON_DIR = ROOT / "ai-json-files"
MANIFEST_PATH = ROOT / "manifest.json"


def is_type(value, schema_type):
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


def validate(instance, schema, path="$"):
    errors = []

    if "anyOf" in schema:
        option_errors = [validate(instance, option, path) for option in schema["anyOf"]]
        return min(option_errors, key=len)

    schema_type = schema.get("type")
    if schema_type is not None:
        allowed = schema_type if isinstance(schema_type, list) else [schema_type]
        if not any(is_type(instance, t) for t in allowed):
            errors.append(f"{path}: expected type {allowed}, got {type(instance).__name__}")
            return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")

        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors.extend(validate(value, props[key], f"{path}.{key}"))

    elif isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(instance):
                errors.extend(validate(item, item_schema, f"{path}[{idx}]"))

    return errors


def schema_specificity(schema):
    score = len(schema.get("required", []))

    for value in schema.get("properties", {}).values():
        if isinstance(value, dict):
            score += schema_specificity(value)

    items = schema.get("items")
    if isinstance(items, dict):
        score += schema_specificity(items)

    if "anyOf" in schema:
        score += max((schema_specificity(part) for part in schema["anyOf"]), default=0)

    return score


def load_schemas():
    schemas = []
    for schema_path in sorted(SCHEMA_DIR.glob("*.json")):
        with schema_path.open("r", encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
        version = int("".join(ch for ch in schema_path.stem if ch.isdigit()) or "0")
        schemas.append(
            {
                "name": schema_path.name,
                "schema": schema,
                "version": version,
                "specificity": schema_specificity(schema),
            }
        )
    return schemas


def classify_json_files(schemas):
    assignments = []
    summary = {schema["name"]: 0 for schema in schemas}

    for json_path in sorted(JSON_DIR.glob("*.json")):
        try:
            with json_path.open("r", encoding="utf-8") as json_file:
                document = json.load(json_file)
        except Exception as exc:
            assignments.append(
                {
                    "file": json_path.name,
                    "assigned_schema": None,
                    "validation_errors": None,
                    "status": "invalid_json",
                    "error": str(exc),
                }
            )
            continue

        scored = []
        for schema in schemas:
            errors = validate(document, schema["schema"])
            scored.append(
                (
                    len(errors),
                    -schema["specificity"],
                    -schema["version"],
                    schema["name"],
                    errors[:5],
                )
            )

        scored.sort()
        best = scored[0]
        assigned_schema = best[3]
        error_count = best[0]

        if error_count == 0:
            summary[assigned_schema] += 1
            assignments.append(
                {
                    "file": json_path.name,
                    "assigned_schema": assigned_schema,
                    "validation_errors": 0,
                    "status": "matched",
                }
            )
        else:
            assignments.append(
                {
                    "file": json_path.name,
                    "assigned_schema": assigned_schema,
                    "validation_errors": error_count,
                    "status": "closest_match",
                    "sample_errors": best[4],
                }
            )

    return assignments, summary


def build_manifest(schemas, assignments, summary):
    assigned_by_schema = {schema["name"]: 0 for schema in schemas}
    unassigned_files = 0
    for item in assignments:
        assigned = item.get("assigned_schema")
        if assigned is None:
            unassigned_files += 1
            continue
        assigned_by_schema[assigned] = assigned_by_schema.get(assigned, 0) + 1

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_folder": "ai-json-files",
        "schema_folder": "inferred_ai_json_schemas",
        "schemas": [schema["name"] for schema in schemas],
        "summary": {
            "total_files": len(assignments),
            "matched_files": sum(1 for item in assignments if item["status"] == "matched"),
            "non_exact_matches": sum(1 for item in assignments if item["status"] != "matched"),
            "exact_match_by_schema": summary,
            "assigned_by_schema": assigned_by_schema,
            "unassigned_files": unassigned_files,
        },
        "files": assignments,
    }


def main():
    schemas = load_schemas()
    assignments, summary = classify_json_files(schemas)
    manifest = build_manifest(schemas, assignments, summary)

    with MANIFEST_PATH.open("w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, indent=2)

    print(f"Wrote {MANIFEST_PATH}")
    print(json.dumps(manifest["summary"], indent=2))


if __name__ == "__main__":
    main()
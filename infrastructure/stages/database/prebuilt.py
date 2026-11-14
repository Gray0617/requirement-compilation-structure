"""Deterministic SQLite DDL lowering for the accepted schema.

The model's schema is already a typed entity graph. Asking a model to translate
every table to SQL adds calls, latency and avoidable syntax errors. This module
performs the same lowering deterministically; the database runtime repair pass
still handles any real initialization failure and application-only constraints.
"""

_TYPE_MAP = {
    "string": "TEXT", "uuid": "TEXT", "date": "TEXT", "datetime": "TEXT", "json": "TEXT",
    "integer": "INTEGER", "boolean": "INTEGER", "number": "REAL",
}


def quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def column_sql(field_name: str, field: dict) -> str:
    """Render one column from the canonical schema properties."""
    kind = str(field.get("type") or "string").lower()
    parts = [quote(field_name), _TYPE_MAP.get(kind, "TEXT")]
    if field.get("primary_key"):
        parts.append("PRIMARY KEY")
    if not field.get("nullable", True):
        parts.append("NOT NULL")
    if field.get("unique") and not field.get("primary_key"):
        parts.append("UNIQUE")
    reference = field.get("references")
    if isinstance(reference, str) and "." in reference:
        table, column = reference.split(".", 1)
        parts.append(f"REFERENCES {quote(table)} ({quote(column)})")
    # SQLite CHECK treats NULL as passing, so nullable booleans remain valid.
    if kind == "boolean":
        parts.append(f"CHECK ({quote(field_name)} IN (0, 1))")
    return " ".join(parts)


def render_table_sql(name: str, entity: dict) -> str:
    """Return a complete CREATE TABLE statement for one entity."""
    fields = entity.get("fields") or {}
    columns = [column_sql(field_name, field) for field_name, field in fields.items()]
    body = ",\n".join("  " + column for column in columns)
    return (
        f'-- Deterministic DDL for {quote(name)} generated from the accepted schema.\n'
        f'CREATE TABLE IF NOT EXISTS {quote(name)} (\n{body}\n);\n'
    )

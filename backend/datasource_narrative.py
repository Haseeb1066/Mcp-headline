"""Fetch published datasource rows via MCP query-datasource and shape for narrative."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.mcp_tableau import call_tool, tool_result_to_text
from backend.tableau_rest import fetch_datasource_fields

LUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _extract_json(text: str) -> Any:
    trimmed = text.strip()
    if not trimmed:
        return None
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        start = trimmed.find("{")
        end = trimmed.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(trimmed[start : end + 1])
            except json.JSONDecodeError:
                pass
        start = trimmed.find("[")
        end = trimmed.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(trimmed[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _is_date_field(f: dict[str, Any]) -> bool:
    dt = str(f.get("dataType") or "").upper()
    name = str(f.get("name") or "").lower()
    if dt in ("DATE", "DATETIME", "DATE_TIME"):
        return True
    return "date" in name or name.endswith(" time")


def _is_measure_field(f: dict[str, Any]) -> bool:
    dt = str(f.get("dataType") or "").upper()
    name = str(f.get("name") or "").lower()
    if _is_date_field(f):
        return False
    if dt in ("INTEGER", "REAL", "FLOAT", "DOUBLE", "NUMBER", "INT"):
        return True
    # Calculated measures often lack dataType — skip formula-only unless numeric-looking name
    if f.get("formula") and any(k in name for k in ("sales", "profit", "amount", "qty", "count", "revenue", "%", "pct")):
        return True
    return False


def pick_query_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a small VDS field list: date + up to 2 dims + up to 2 measures."""
    dates = [f for f in fields if _is_date_field(f)]
    measures = [f for f in fields if _is_measure_field(f)]
    dims = [
        f
        for f in fields
        if not _is_date_field(f)
        and not _is_measure_field(f)
        and f.get("typename") != "Notice"
        and f.get("name") != "_truncation"
    ]

    query_fields: list[dict[str, Any]] = []
    if dates:
        query_fields.append({"fieldCaption": dates[0]["name"]})
    for d in dims[:2]:
        query_fields.append({"fieldCaption": d["name"]})
    for m in measures[:2]:
        query_fields.append(
            {
                "fieldCaption": m["name"],
                "function": "SUM",
                "fieldAlias": m["name"],
            }
        )
    if not query_fields and fields:
        for f in fields[:4]:
            if f.get("name") and f.get("name") != "_truncation":
                query_fields.append({"fieldCaption": f["name"]})
    return query_fields


def _rows_from_query_payload(payload: Any) -> tuple[list[dict[str, Any]], list[list[Any]]]:
    """Normalize MCP/VDS response into columns + row values."""
    if payload is None:
        return [], []

    # Common shapes: { data: [...], columns: [...] } or { rows: [...] } or list of dicts
    if isinstance(payload, dict) and payload.get("isError"):
        raise RuntimeError(str(payload.get("content") or payload))

    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        keys = list(payload[0].keys())
        columns = [{"index": i, "fieldName": k, "dataType": "string", "name": k} for i, k in enumerate(keys)]
        rows = [[row.get(k) for k in keys] for row in payload]
        return columns, rows

    if isinstance(payload, dict):
        for key in ("data", "rows", "result", "table"):
            inner = payload.get(key)
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                return _rows_from_query_payload(inner)

        # { columns: [...], data: [[...]] }
        cols_raw = payload.get("columns") or payload.get("fields")
        data_raw = payload.get("data") or payload.get("rows")
        if isinstance(cols_raw, list) and isinstance(data_raw, list):
            columns = []
            for i, c in enumerate(cols_raw):
                if isinstance(c, dict):
                    name = c.get("fieldCaption") or c.get("name") or c.get("fieldName") or f"col_{i}"
                    dtype = c.get("dataType") or "string"
                else:
                    name = str(c)
                    dtype = "string"
                columns.append({"index": i, "fieldName": name, "dataType": dtype, "name": name})
            rows: list[list[Any]] = []
            for row in data_raw:
                if isinstance(row, list):
                    rows.append(row)
                elif isinstance(row, dict):
                    rows.append([row.get(c["fieldName"]) for c in columns])
            return columns, rows

    raise RuntimeError(f"Unrecognized query-datasource payload shape: {str(payload)[:300]}")


async def list_datasources_via_mcp() -> list[dict[str, Any]]:
    result = await call_tool("list-datasources", {})
    text = tool_result_to_text(result)
    if result.get("isError"):
        raise RuntimeError(text)
    payload = _extract_json(text)
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("datasources", "data", "items"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "id": row.get("id") or row.get("luid") or "",
                "name": row.get("name") or "",
                "projectName": (row.get("project") or {}).get("name")
                if isinstance(row.get("project"), dict)
                else row.get("projectName") or "",
                "isPublished": True,
                "source": "mcp",
            }
        )
    return out


# Fallback captions when Metadata API is unavailable (common AP / finance fields)
_FALLBACK_FIELD_SETS: list[list[str]] = [
    [
        "Outstanding Amount",
        "Cleared Flag",
        "Due Date",
        "Invoice Date",
        "Creditor",
        "Aging Category (Level 1)",
        "Aging Category (Level 2)",
        "Aging Category (Level 3)",
    ],
    [
        "Outstanding Amount",
        "Cleared Flag",
        "Due Date",
        "Invoice Date",
        "Aging Category (Level 1)",
        "Aging Category (Level 2)",
        "Aging Category (Level 3)",
    ],
    ["Outstanding Amount", "Cleared Flag", "Due Date", "Creditor", "Aging Category (Level 1)"],
    ["Outstanding Amount", "Cleared Flag", "Due Date"],
    ["Outstanding Amount", "Cleared Flag"],
    ["Order Date", "Sales", "Category"],
]


async def _resolve_luid_and_name(
    identifier: str,
    datasource_luid: Optional[str],
    datasource_name: Optional[str],
) -> tuple[str, str, list[dict[str, Any]], str]:
    """Returns luid, name, fields, source ('metadata'|'mcp'|'fallback')."""
    try:
        meta = fetch_datasource_fields(identifier)
        matches = meta.get("matches") or []
        if matches:
            ds = matches[0]
            luid = ds.get("luid") or (
                datasource_luid if datasource_luid and LUID_RE.match(datasource_luid) else ""
            )
            name = ds.get("name") or datasource_name or identifier
            fields = ds.get("fields") or []
            if luid:
                return luid, name, fields, "metadata"
    except Exception:
        pass

    # MCP list-datasources fallback
    listed = await list_datasources_via_mcp()
    want = identifier.casefold()
    match = next(
        (
            d
            for d in listed
            if (d.get("id") or "").casefold() == want or (d.get("name") or "").casefold() == want
        ),
        None,
    )
    if not match and datasource_name:
        want_n = datasource_name.casefold()
        match = next((d for d in listed if (d.get("name") or "").casefold() == want_n), None)
    if match and match.get("id"):
        return match["id"], match.get("name") or identifier, [], "mcp"

    if datasource_luid and LUID_RE.match(datasource_luid):
        return datasource_luid, datasource_name or identifier, [], "fallback"

    raise RuntimeError(
        f"Could not resolve datasource {identifier!r}. Metadata API may be disabled; "
        "confirm the published datasource name/LUID via list-datasources."
    )


async def fetch_datasource_table(
    *,
    datasource_luid: Optional[str] = None,
    datasource_name: Optional[str] = None,
    limit: int = 5000,
    field_captions: Optional[list[str]] = None,
) -> dict[str, Any]:
    identifier = (datasource_luid or datasource_name or "").strip()
    if not identifier:
        raise ValueError("datasourceLuid or datasourceName is required")

    luid, name, fields, source = await _resolve_luid_and_name(
        identifier, datasource_luid, datasource_name
    )

    query_fields = pick_query_fields(fields) if fields else []
    if field_captions:
        query_fields = []
        for caption in field_captions:
            query_fields.append({"fieldCaption": caption})

    attempts: list[list[dict[str, Any]]] = []
    if query_fields:
        attempts.append(query_fields)
    if not fields:
        for captions in _FALLBACK_FIELD_SETS:
            attempts.append(
                [
                    (
                        {"fieldCaption": c, "function": "SUM", "fieldAlias": c}
                        if re.search(r"amount|sales|balance|total|qty|value", c, re.I)
                        else {"fieldCaption": c}
                    )
                    for c in captions
                ]
            )

    last_error = "No queryable fields"
    for qf in attempts:
        arguments = {
            "datasourceLuid": luid,
            "query": {
                "fields": qf,
            },
        }
        result = await call_tool("query-datasource", arguments)
        text = tool_result_to_text(result)
        if result.get("isError"):
            last_error = text[:800]
            continue
        payload = _extract_json(text)
        try:
            columns, rows = _rows_from_query_payload(payload)
        except Exception as e:
            last_error = str(e)
            continue
        return {
            "columns": columns,
            "rows": rows,
            "datasourceName": name,
            "datasourceLuid": luid,
            "queryFields": qf,
            "fieldCount": len(fields),
            "fieldSource": source,
        }

    raise RuntimeError(f"query-datasource failed for {name!r}: {last_error}")

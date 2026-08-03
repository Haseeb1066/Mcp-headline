"""Fetch workbook view data via MCP and shape it for narrative analysis."""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Optional

from backend.mcp_tableau import call_tool, tool_result_to_text
from backend.tableau_rest import resolve_workbook


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
                return None
    return None


def _parse_csv_table(text: str) -> tuple[list[dict[str, Any]], list[list[Any]]]:
    # get-view-data often returns a JSON-encoded CSV string
    raw = text.strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = raw[1:-1].encode("utf-8").decode("unicode_escape")

    reader = csv.reader(io.StringIO(raw))
    rows_raw = list(reader)
    if not rows_raw:
        return [], []
    headers = [h.strip() or f"col_{i}" for i, h in enumerate(rows_raw[0])]
    columns = [
        {"index": i, "fieldName": h, "dataType": "string", "name": h} for i, h in enumerate(headers)
    ]
    rows: list[list[Any]] = []
    for line in rows_raw[1:]:
        # pad / trim
        padded = list(line) + [None] * max(0, len(headers) - len(line))
        cells: list[Any] = []
        for cell in padded[: len(headers)]:
            if cell is None or cell == "":
                cells.append(None)
                continue
            # strip currency / percent noise for numeric parse later
            cells.append(cell)
        rows.append(cells)

    # Heuristic: mark numeric-looking columns
    for col in columns:
        idx = col["index"]
        sample = [r[idx] for r in rows[:50] if r[idx] is not None]
        if not sample:
            continue
        num_hits = 0
        for v in sample:
            s = str(v).strip().replace(",", "").replace("$", "").replace("%", "")
            try:
                float(s)
                num_hits += 1
            except ValueError:
                pass
        if sample and num_hits / len(sample) >= 0.7:
            col["dataType"] = "float"
            for r in rows:
                if r[idx] is None:
                    continue
                s = str(r[idx]).strip().replace(",", "").replace("$", "").replace("%", "")
                try:
                    r[idx] = float(s)
                except ValueError:
                    pass
    return columns, rows


def _views_from_workbook_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    views = data.get("views") if isinstance(data, dict) else None
    if isinstance(views, dict):
        rows = views.get("view") or []
        if isinstance(rows, dict):
            rows = [rows]
        return [v for v in rows if isinstance(v, dict)]
    if isinstance(views, list):
        return [v for v in views if isinstance(v, dict)]
    return []


async def fetch_workbook_views(
    *,
    workbook_id: Optional[str] = None,
    content_url: Optional[str] = None,
    name: Optional[str] = None,
) -> dict[str, Any]:
    if not workbook_id:
        wb = resolve_workbook(workbook_id=None, name=name, content_url=content_url)
        workbook_id = wb.id
        content_url = wb.content_url
        name = wb.name
    else:
        wb = None

    result = await call_tool("get-workbook", {"workbookId": workbook_id})
    text = tool_result_to_text(result)
    if result.get("isError"):
        raise RuntimeError(text[:800])
    payload = _extract_json(text) or {}
    views = _views_from_workbook_payload(payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return {
        "workbook": {
            "id": workbook_id,
            "name": (data.get("name") if isinstance(data, dict) else None) or name or "",
            "contentUrl": (data.get("contentUrl") if isinstance(data, dict) else None)
            or content_url
            or "",
            "defaultViewId": (data.get("defaultViewId") if isinstance(data, dict) else None),
        },
        "views": [
            {
                "id": v.get("id") or "",
                "name": v.get("name") or "",
                "contentUrl": v.get("contentUrl") or "",
                "viewUrlName": v.get("viewUrlName") or "",
            }
            for v in views
        ],
    }


async def fetch_view_table(
    *,
    view_id: Optional[str] = None,
    view_name: Optional[str] = None,
    workbook_id: Optional[str] = None,
    content_url: Optional[str] = None,
    workbook_name: Optional[str] = None,
) -> dict[str, Any]:
    meta = await fetch_workbook_views(
        workbook_id=workbook_id,
        content_url=content_url,
        name=workbook_name,
    )
    views = meta["views"]
    chosen = None
    if view_id:
        chosen = next((v for v in views if v["id"] == view_id), None)
    if not chosen and view_name:
        vn = view_name.strip().casefold()
        chosen = next((v for v in views if v["name"].casefold() == vn), None)
        if not chosen:
            chosen = next((v for v in views if vn in v["name"].casefold()), None)
    if not chosen:
        default_id = meta["workbook"].get("defaultViewId")
        chosen = next((v for v in views if v["id"] == default_id), None) or (views[0] if views else None)
    if not chosen:
        raise RuntimeError("No views found on workbook")

    result = await call_tool("get-view-data", {"viewId": chosen["id"]})
    text = tool_result_to_text(result)
    if result.get("isError"):
        raise RuntimeError(f"get-view-data failed: {text[:800]}")

    columns, rows = _parse_csv_table(text)
    return {
        "columns": columns,
        "rows": rows,
        "view": chosen,
        "workbook": meta["workbook"],
        "views": views,
    }


async def query_datasource_simple(
    datasource_luid: str,
    field_captions: list[str],
    *,
    limit: int = 5000,
) -> dict[str, Any]:
    """Query published datasource without Metadata API, using known field captions."""
    from backend.datasource_narrative import _rows_from_query_payload, _extract_json

    fields: list[dict[str, Any]] = []
    for caption in field_captions:
        # Prefer SUM for amount-like measures
        if re.search(r"amount|balance|total|qty|quantity|count|value", caption, re.I):
            fields.append({"fieldCaption": caption, "function": "SUM", "fieldAlias": caption})
        else:
            fields.append({"fieldCaption": caption})

    arguments = {
        "datasourceLuid": datasource_luid,
        "query": {"fields": fields},
    }
    result = await call_tool("query-datasource", arguments)
    text = tool_result_to_text(result)
    if result.get("isError"):
        raise RuntimeError(f"query-datasource failed: {text[:800]}")
    payload = _extract_json(text)
    columns, rows = _rows_from_query_payload(payload)
    return {"columns": columns, "rows": rows, "queryFields": fields, "datasourceLuid": datasource_luid}

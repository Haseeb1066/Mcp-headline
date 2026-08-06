"""FastAPI entry: narrative insights for workbook worksheets + published datasources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import env, has_tableau_creds
from backend.datasource_narrative import fetch_datasource_table, list_datasources_via_mcp
from backend.narrative import analyze_table
from backend.polish import generate_insight_sections, polish_summary
from backend.tableau_rest import (
    fetch_datasource_fields,
    list_published_datasources,
    list_workbook_connections,
    probe_tableau_sign_in,
    resolve_workbook,
)
from backend.workbook_narrative import fetch_view_table, fetch_workbook_views

ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "dist" / "web"


class ColumnIn(BaseModel):
    index: int
    fieldName: str
    dataType: str = "string"
    name: Optional[str] = None


class NarrativeRequest(BaseModel):
    columns: list[ColumnIn]
    rows: list[list[Any]] = Field(default_factory=list)
    worksheetName: str = "Worksheet"
    dashboardName: str = ""
    workbookName: str = ""
    datasourceName: str = ""
    datasourceLuid: str = ""
    dataSource: str = "workbook"  # workbook | datasource
    polish: bool = True


class DatasourceNarrativeRequest(BaseModel):
    datasourceLuid: Optional[str] = None
    datasourceName: Optional[str] = None
    workbookName: str = ""
    dashboardName: str = ""
    limit: int = 5000
    polish: bool = True
    fieldCaptions: Optional[list[str]] = None


class WorkbookNarrativeRequest(BaseModel):
    workbookId: Optional[str] = None
    contentUrl: Optional[str] = None
    workbookName: Optional[str] = None
    viewId: Optional[str] = None
    viewName: Optional[str] = None
    dashboardName: str = ""
    polish: bool = True


app = FastAPI(title="Tableau Narrative Insights", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _run_analysis(
    *,
    columns: list[dict[str, Any]],
    rows: list[list[Any]],
    worksheet_name: str,
    dashboard_name: str,
    workbook_name: str,
    polish: bool,
    extra_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    analysis = analyze_table(
        columns=columns,
        rows=rows,
        worksheet_name=worksheet_name,
        dashboard_name=dashboard_name,
        workbook_name=workbook_name,
    )
    if extra_context:
        analysis.setdefault("context", {}).update(extra_context)
    if polish and env("OPENAI_API_KEY"):
        polished = polish_summary(analysis)
        if polished:
            analysis["summary"] = polished
            analysis["summarySource"] = "llm"
    return analysis


@app.get("/api/health")
def health() -> dict[str, Any]:
    tableau = probe_tableau_sign_in()
    return {
        "ok": True,
        "hasOpenAi": bool(env("OPENAI_API_KEY")),
        "hasTableau": has_tableau_creds(),
        "hasWebUi": WEB_DIST.is_dir() and (WEB_DIST / "index.html").is_file(),
        "model": env("OPENAI_MODEL", "gpt-4o-mini") if env("OPENAI_API_KEY") else None,
        "backend": "python",
        "service": "tableau-narrative",
        **tableau,
    }


@app.get("/api/workbooks/resolve")
def api_resolve_workbook(
    workbookId: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    contentUrl: Optional[str] = Query(None),
    projectName: Optional[str] = Query(None),
) -> dict[str, Any]:
    if not has_tableau_creds():
        raise HTTPException(status_code=503, detail="Tableau credentials not configured in .env")
    if not any([(workbookId or "").strip(), (name or "").strip(), (contentUrl or "").strip()]):
        raise HTTPException(status_code=400, detail="Provide workbookId, name, or contentUrl")
    try:
        wb = resolve_workbook(
            workbook_id=workbookId,
            name=name,
            content_url=contentUrl,
            project_name=projectName,
        )
        return {"workbook": wb.to_api_dict()}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/workbooks/{workbook_id}/connections")
def api_workbook_connections(workbook_id: str) -> dict[str, Any]:
    if not has_tableau_creds():
        raise HTTPException(status_code=503, detail="Tableau credentials not configured in .env")
    try:
        connections = list_workbook_connections(workbook_id)
        return {"workbookId": workbook_id, "connections": connections}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/api/datasources")
async def api_list_datasources(
    workbookId: Optional[str] = Query(None),
) -> dict[str, Any]:
    if not has_tableau_creds():
        raise HTTPException(status_code=503, detail="Tableau credentials not configured in .env")

    published: list[dict[str, Any]] = []
    connections: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        published = list_published_datasources()
    except Exception as e:
        errors.append(f"REST datasources: {e}")
        try:
            published = await list_datasources_via_mcp()
        except Exception as e2:
            errors.append(f"MCP datasources: {e2}")

    if workbookId:
        try:
            connections = list_workbook_connections(workbookId)
        except Exception as e:
            errors.append(f"workbook connections: {e}")

    # Prefer workbook-linked datasources when available
    linked_ids = {c.get("datasourceId") for c in connections if c.get("datasourceId")}
    linked_names = {(c.get("datasourceName") or "").casefold() for c in connections if c.get("datasourceName")}
    workbook_ds = [
        d
        for d in published
        if (d.get("id") and d["id"] in linked_ids)
        or (d.get("name") and d["name"].casefold() in linked_names)
    ]
    # Include connection datasources even if not in the published catalog
    have_ids = {d.get("id") for d in workbook_ds if d.get("id")}
    have_names = {(d.get("name") or "").casefold() for d in workbook_ds}
    for c in connections:
        cid = c.get("datasourceId") or ""
        cname = c.get("datasourceName") or ""
        if not cid and not cname:
            continue
        if cid in have_ids or cname.casefold() in have_names:
            continue
        workbook_ds.append(
            {
                "id": cid,
                "name": cname or cid,
                "isPublished": True,
                "source": "workbook-connection",
            }
        )
        if cid:
            have_ids.add(cid)
        if cname:
            have_names.add(cname.casefold())

    return {
        "datasources": workbook_ds if connections else published,
        "allPublished": published,
        "workbookConnections": connections,
        "scopedToWorkbook": bool(connections),
        "errors": errors,
    }


@app.get("/api/datasource-fields")
def api_datasource_fields(
    luid: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
) -> dict[str, Any]:
    if not has_tableau_creds():
        raise HTTPException(status_code=503, detail="Tableau credentials not configured in .env")
    identifier = (luid or name or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Provide luid or name")
    try:
        return fetch_datasource_fields(identifier)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/api/narrative")
def api_narrative(body: NarrativeRequest) -> dict[str, Any]:
    if not body.columns:
        raise HTTPException(status_code=400, detail="columns are required")

    max_rows = 20_000
    rows = body.rows[:max_rows]
    columns = [
        {
            "index": c.index,
            "fieldName": c.fieldName,
            "dataType": c.dataType,
            "name": c.name or c.fieldName,
        }
        for c in body.columns
    ]

    label = body.worksheetName
    if body.dataSource == "datasource" and body.datasourceName:
        label = body.datasourceName

    analysis = _run_analysis(
        columns=columns,
        rows=rows,
        worksheet_name=label,
        dashboard_name=body.dashboardName,
        workbook_name=body.workbookName,
        polish=body.polish,
        extra_context={
            "dataSource": body.dataSource,
            "datasourceName": body.datasourceName or None,
            "datasourceLuid": body.datasourceLuid or None,
        },
    )
    analysis["truncated"] = len(body.rows) > max_rows
    if body.polish and env("OPENAI_API_KEY"):
        sections = generate_insight_sections(analysis, columns, rows)
        if sections:
            analysis["insightSections"] = sections
    return analysis


@app.get("/api/workbooks/{workbook_id}/views")
async def api_workbook_views(workbook_id: str) -> dict[str, Any]:
    if not has_tableau_creds():
        raise HTTPException(status_code=503, detail="Tableau credentials not configured in .env")
    try:
        return await fetch_workbook_views(workbook_id=workbook_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/api/narrative/from-workbook")
async def api_narrative_from_workbook(body: WorkbookNarrativeRequest) -> dict[str, Any]:
    if not has_tableau_creds():
        raise HTTPException(status_code=503, detail="Tableau credentials not configured in .env")
    if not any([(body.workbookId or "").strip(), (body.contentUrl or "").strip(), (body.workbookName or "").strip()]):
        raise HTTPException(status_code=400, detail="workbookId, contentUrl, or workbookName required")
    try:
        table = await fetch_view_table(
            view_id=body.viewId,
            view_name=body.viewName,
            workbook_id=body.workbookId,
            content_url=body.contentUrl,
            workbook_name=body.workbookName,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    wb = table["workbook"]
    view = table["view"]
    analysis = _run_analysis(
        columns=table["columns"],
        rows=table["rows"],
        worksheet_name=view.get("name") or "View",
        dashboard_name=body.dashboardName or view.get("name") or "",
        workbook_name=wb.get("name") or "",
        polish=body.polish,
        extra_context={
            "dataSource": "workbook",
            "workbookId": wb.get("id"),
            "contentUrl": wb.get("contentUrl"),
            "viewId": view.get("id"),
            "viewName": view.get("name"),
        },
    )
    if body.polish and env("OPENAI_API_KEY"):
        sections = generate_insight_sections(analysis, table["columns"], table["rows"])
        if sections:
            analysis["insightSections"] = sections
    analysis["views"] = table.get("views")
    return analysis


@app.post("/api/narrative/from-datasource")
async def api_narrative_from_datasource(body: DatasourceNarrativeRequest) -> dict[str, Any]:
    if not has_tableau_creds():
        raise HTTPException(
            status_code=503,
            detail="Tableau credentials required to query published datasources. Set TABLEAU_* in .env",
        )
    if not (body.datasourceLuid or body.datasourceName):
        raise HTTPException(status_code=400, detail="datasourceLuid or datasourceName required")

    try:
        table = await fetch_datasource_table(
            datasource_luid=body.datasourceLuid,
            datasource_name=body.datasourceName,
            limit=body.limit,
            field_captions=body.fieldCaptions,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    analysis = _run_analysis(
        columns=table["columns"],
        rows=table["rows"],
        worksheet_name=table["datasourceName"],
        dashboard_name=body.dashboardName,
        workbook_name=body.workbookName,
        polish=body.polish,
        extra_context={
            "dataSource": "datasource",
            "datasourceName": table["datasourceName"],
            "datasourceLuid": table["datasourceLuid"],
            "queryFields": table.get("queryFields"),
            "fieldCount": table.get("fieldCount"),
            "fieldSource": table.get("fieldSource"),
        },
    )
    if body.polish and env("OPENAI_API_KEY"):
        sections = generate_insight_sections(analysis, table["columns"], table["rows"])
        if sections:
            analysis["insightSections"] = sections
    analysis["queryFields"] = table.get("queryFields")
    return analysis


def _spa_html_response() -> FileResponse:
    response = FileResponse(WEB_DIST / "index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


if WEB_DIST.is_dir():
    assets = WEB_DIST / "assets"

    @app.get("/assets/{asset_path:path}")
    async def spa_assets(asset_path: str):
        file_path = assets / asset_path
        if not file_path.is_file():
            raise HTTPException(status_code=404)
        response = FileResponse(file_path)
        if re.search(r"index-[A-Za-z0-9_-]+\.(js|css)$", asset_path):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.get("/")
    async def spa_index(_request: Request):
        return _spa_html_response()

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(status_code=404)
        file_path = WEB_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return _spa_html_response()
